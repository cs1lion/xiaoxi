"""S1（issue #27）：语音快环延迟基准——三个指标，可重复跑，Mock 模式量链路开销。

指标
  A 快环首字延迟：WS 发出文本 turn → 收到首个 audio 块（附 asr/thinking/
    text_delta/speech_plan/done 分段拆解，便于定位时间花在哪）。
  B 打断响应：barge_in 发出 → playback_stopped 到达（附 interrupted 分段、
    命中率、打断后旧轮 audio 违规计数）。
  C 记忆检索耗时：进程内直呼 memory 检索层（三档置信度装配路径），
    冷（TF-IDF 重建）/热（索引缓存）/分档命中/无命中四个桶。

跑法（仓库根，虚拟环境）：
  .venv/Scripts/python.exe scripts/bench_voice_loop.py
  .venv/Scripts/python.exe scripts/bench_voice_loop.py --only a,b --samples-a 40
  结果 JSON 落 tmp/bench-voice-loop-<ts>/result.json；stdout 是人读报告。

设计口径（写进 docs/LATENCY_BASELINE.md 的前提，改这里要同步改基线文档）：
  - 自起独立端口真实服务（默认 8031，--port 可改；拒绝 8000/8010），不碰用户端口。
  - LLM 走 Mock（无 Key 自动 offline，mock 流式按 6 字/20ms 吐字——这是 mock
    桩自带的节奏，测得的首字延迟含它，解读时要知道）。
  - TTS 没有离线引擎：silent 引擎产 0 字节，deliver_reply 会跳过整段音频循环，
    指标 A 就无从测起。因此基准用 uvicorn 加载 bench_entry 包装模块，把
    TTSEngine.synthesize_profile 换成确定性的离线 WAV 桩（0.12s 220Hz 正弦，
    不联网、无延迟），生产文件零改动。指标 B 单独起一个带 BENCH_TTS_DELAY_MS
    的实例，人为给合成留一个在途窗口，否则 Mock 链路太快、打断永远追不上收轮。
  - 记忆库：临时 DB 预置三档（stated/inferred/uncertain）+ 中性填充记录，
    检索走真实代码路径；不碰 data/memory.db。
  - 计时一律 time.perf_counter()，客户端侧含 WS 往返——这是"端到端链路开销"
    的口径；done.latency_ms 是服务端自报口径，两者差≈传输往返。
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FORBIDDEN_PORTS = {8000, 8010}  # 红线：用户在用的端口，碰都不能碰
DEFAULT_PORT = 8031

# 指标 A 用的问句：mock LLM 对问句回固定短句（确定性），同时命中预置的
# stated 记忆，让 _turn_context 的检索真实发生。
QUERY_HIT = "还记得我的猫叫什么名字吗？"
# 指标 B 需要 Mock 链路里有一段"在途合成窗口"：mock 回复加长 + TTS 桩延迟。
BARGE_REPLY = (
    "大概吧，我记得一些。那时候我们聊过好几回，每次都挺开心的。"
    "你还问过我猫的事、吉他的事，我都记着呢。要是有记错的地方，你直接说，我改。"
)

# 指标 C 的语料构造：三档锚点记录 + 无关填充。填充避开锚点关键词。
TIER_ANCHORS = {
    "stated": ("宠物:猫的名字", "用户养的猫名字叫年糕", "猫的名字叫什么"),
    "inferred": ("作息:写代码时段", "用户可能习惯深夜写代码", "用户习惯什么时段写代码"),
    "uncertain": ("爱好:乐器", "用户好像在学吉他", "用户最近在学什么乐器"),
}
# 指标 C 的负向路径探针：故意用语料里不可能出现的 ASCII 字母，保证
# TF-IDF 相似度为 0（中文短查询的 char 1-3gram 很容易撞出假相似——
# 见 docs/LATENCY_BASELINE.md 发现 F-1 / issue #30，真实行为不是桩问题）。
QUERY_MISS = "quantum entanglement lab coordinates"


def stats(samples: list[float]) -> dict:
    if not samples:
        return {"n": 0}
    ordered = sorted(samples)
    n = len(ordered)

    def pct(p: float) -> float:
        idx = min(n - 1, max(0, math.ceil(p * n) - 1))
        return round(ordered[idx], 2)

    return {"n": n, "min": round(ordered[0], 2), "p50": pct(0.50), "p90": pct(0.90),
            "p95": pct(0.95), "max": round(ordered[-1], 2),
            "mean": round(sum(ordered) / n, 2)}


# --------------------------------------------------------------------------
# 临时工作区：bench DB + bench_entry 包装模块
# --------------------------------------------------------------------------

BENCH_ENTRY = '''"""bench-only uvicorn entry：真实 server.app + 离线 provider 桩。

只替换两处外部供应商行为，管线（orchestrator/记忆/交付/WS）全是生产代码：
  - TTSEngine.synthesize_profile -> 确定性离线 WAV（220Hz 正弦 0.12s），
    BENCH_TTS_DELAY_MS 给合成加在途窗口（指标 B 用）。
  - LLM._mock -> BENCH_MOCK_REPLY 非空时固定回复（指标 B 需要长回复）。
"""
import asyncio
import math
import os
import struct

_RATE = 24000
_DELAY = float(os.environ.get("BENCH_TTS_DELAY_MS", "0")) / 1000.0


def _wav(seconds: float = 0.12, freq: float = 220.0) -> bytes:
    count = int(_RATE * seconds)
    pcm = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * freq * i / _RATE)))
        for i in range(count)
    )
    return struct.pack(
        "<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(pcm), b"WAVE", b"fmt ",
        16, 1, 1, _RATE, _RATE * 2, 2, 16, b"data", len(pcm)) + pcm


_WAV = _wav()

from server.speech.tts import TTSEngine  # noqa: E402


async def _bench_synthesize_profile(self, text, profile):
    text = (text or "").strip()
    if not text:
        return b""
    if _DELAY > 0:
        await asyncio.sleep(_DELAY)
    return _WAV


TTSEngine.synthesize_profile = _bench_synthesize_profile

_reply = os.environ.get("BENCH_MOCK_REPLY", "")
if _reply:
    import json as _json  # noqa: E402
    from server.llm import LLM  # noqa: E402

    def _bench_mock(self, messages):
        # 与原版同款 JSON 分支：后台记忆抽取要的是空 facts JSON，不是台词。
        if "JSON" in messages[0]["content"] or "只输出 JSON" in messages[0]["content"]:
            return _json.dumps({"facts": [], "episodes": [], "todos": [],
                                "self_states": []}, ensure_ascii=False)
        return _reply

    LLM._mock = _bench_mock

from server.app import app  # noqa: E402,F401
'''


def seed_corpus(store: "MemoryStore", size: int) -> None:  # noqa: F821 - 延迟导入
    """预置三档锚点 + 无关填充到给定 store。size = 总记录数（含锚点）。"""
    for tier, (key, value, _q) in TIER_ANCHORS.items():
        store.upsert(kind="fact", key=key, value=value, source=tier,
                     importance=0.8 if tier == "stated" else 0.5,
                     session_id="bench-seed", evidence_state="manual")
    filler = max(0, size - len(TIER_ANCHORS))
    for i in range(filler):
        store.upsert(kind="fact", key=f"归档:文件夹编号{i:04d}",
                     value=f"库存记录第{i}条：文件夹编号{i:04d}的归档说明与签收单",
                     source="stated", importance=0.3, session_id="bench-seed",
                     evidence_state="manual")


_ISOLATION_DONE = False


def make_workspace(parent: Path, tag: str, corpus: int) -> tuple[Path, Path]:
    """建 <parent>/<tag>/，写入 bench_entry.py，预置 <tag>/memory.db。"""
    global _ISOLATION_DONE
    ws = parent / tag
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "bench_entry.py").write_text(BENCH_ENTRY, encoding="utf-8")
    db = ws / "memory.db"
    if db.exists():
        db.unlink()
    # 首次 import server.memory.* 之前必须先钉住 DB_PATH：模块级 store 单例在
    # import 时就会打开默认库。首次隔离后，后续调用只需换路径（单例已指向
    # 临时库；这里用显式 MemoryStore(str(db))，不经过单例）。
    if not _ISOLATION_DONE:
        if "server.memory.store" in sys.modules:  # pragma: no cover - 防御性
            raise RuntimeError("memory store imported before bench isolation")
        _ISOLATION_DONE = True
    os.environ["DB_PATH"] = str(db)
    from server.memory.store import MemoryStore
    store = MemoryStore(str(db))
    seed_corpus(store, corpus)
    store._conn.close()
    return ws, db


# --------------------------------------------------------------------------
# 服务端生命周期
# --------------------------------------------------------------------------

class Server:
    """uvicorn 子进程：真实 server.app，bench_entry 只换 provider 桩。"""

    def __init__(self, ws: Path, port: int, *, tts_delay_ms: float, mock_reply: str):
        self.port = port
        self.base = f"http://127.0.0.1:{port}"
        env = os.environ.copy()
        env.update({
            "DB_PATH": str(ws / "memory.db"),
            "LLM_PROVIDER": "mock",
            "LLM_API_KEY": "",
            "BENCH_TTS_DELAY_MS": str(tts_delay_ms),
            "BENCH_MOCK_REPLY": mock_reply,
            "PYTHONUNBUFFERED": "1",
            # Windows 下 uvicorn --app-dir 只是 sys.path 注入，不需要 PYTHONPATH
        })
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "bench_entry:app",
             "--host", "127.0.0.1", "--port", str(port),
             "--app-dir", str(ws), "--log-level", "warning"],
            cwd=str(ROOT), env=env,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def wait_health(self, deadline_s: float = 90.0) -> str:
        import urllib.request
        deadline = time.monotonic() + deadline_s
        err = ""
        while time.monotonic() < deadline:
            if self.proc.poll() is not None:
                err = (self.proc.stderr.read() if self.proc.stderr else b"")[-1500:]
                raise RuntimeError(f"bench server died: {err!r}")
            try:
                with urllib.request.urlopen(f"{self.base}/api/health", timeout=2) as r:
                    if r.status == 200:
                        return ""
            except Exception as exc:  # noqa: BLE001 - 起不来时继续等
                err = str(exc)
            time.sleep(0.4)
        raise RuntimeError(f"bench server health timeout: {err}")

    def stop(self) -> None:
        if self.proc.poll() is None:
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"],
                               capture_output=True)
            else:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=10)
                except subprocess.TimeoutExpired:  # pragma: no cover
                    self.proc.kill()
        for stream in (self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()


# --------------------------------------------------------------------------
# WS 采集
# --------------------------------------------------------------------------

async def run_turn(ws, query: str, *, barge_after_first_audio: bool = False,
                   drain_ms: int = 0):
    """一轮文本 turn 的完整事件时间线。

    返回 dict：各事件类型首次到达的相对秒（t0 = text 发出前一刻），
    barge 模式下另带 barge/interrupted/playback_stopped/violations。
    """
    turn_id = f"bench-{time.monotonic_ns()}"
    first: dict[str, float] = {}
    audio_chunks = 0
    audio_bytes = 0
    done_latency = None
    t0 = time.perf_counter()
    await ws.send(json.dumps({"type": "text", "text": query, "turn_id": turn_id},
                             ensure_ascii=False))
    first["turn_sent"] = 0.0

    barge_sent_at = None
    interrupted_at = None
    playback_stopped_at = None
    violations: list[str] = []
    turn_over = False

    def rel() -> float:
        return round(time.perf_counter() - t0, 4)

    while True:
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=20)
        except (asyncio.TimeoutError, TimeoutError):
            raise RuntimeError(f"turn timed out; got={list(first)}")
        if isinstance(raw, bytes):  # pragma: no cover - 服务端不发二进制
            continue
        event = json.loads(raw)
        kind = event.get("type")
        if event.get("turn_id") not in (None, turn_id):
            continue  # 别的连接级事件（ready/config/v2 广播等）
        first.setdefault(kind, rel())
        if kind == "audio":
            audio_chunks += 1
            audio_bytes += len(base64.b64decode(event.get("data") or ""))
            if barge_after_first_audio and barge_sent_at is None:
                barge_sent_at = time.perf_counter()
                await ws.send(json.dumps({"type": "barge_in", "turn_id": turn_id}))
                first["barge_sent"] = round(barge_sent_at - t0, 4)
        elif kind == "done":
            done_latency = event.get("latency_ms")
            turn_over = True
            break
        elif kind == "interrupted":
            interrupted_at = time.perf_counter()
        elif kind == "playback_stopped":
            playback_stopped_at = time.perf_counter()
            if barge_after_first_audio:
                # 打断生效后短暂静默窗口：旧轮不得再有任何 audio/text_delta
                deadline = time.monotonic() + drain_ms / 1000
                while time.monotonic() < deadline:
                    try:
                        raw2 = await asyncio.wait_for(ws.recv(), timeout=0.05)
                    except (asyncio.TimeoutError, TimeoutError):
                        continue
                    if isinstance(raw2, str):
                        ev2 = json.loads(raw2)
                        if ev2.get("turn_id") == turn_id and ev2.get("type") in (
                                "audio", "text_delta", "speech_plan"):
                            violations.append(ev2.get("type"))
                break
        elif kind == "error":
            raise RuntimeError(f"server error event: {event}")
    result = {"first": first, "audio_chunks": audio_chunks,
              "audio_bytes": audio_bytes, "done_latency_ms": done_latency}
    if barge_after_first_audio:
        result.update({
            "barge_to_interrupted_ms":
                round((interrupted_at - barge_sent_at) * 1000, 2) if interrupted_at else None,
            "barge_to_playback_stopped_ms":
                round((playback_stopped_at - barge_sent_at) * 1000, 2) if playback_stopped_at else None,
            "violations": violations,
            # turn 先于打断收轮（done 先到）：链路快过打断往返时的 miss 形态
            "done_after_barge": turn_over and barge_sent_at is not None,
        })
    return result


# --------------------------------------------------------------------------
# 指标 A：快环首字延迟（text -> first audio）
# --------------------------------------------------------------------------

async def bench_first_audio(port: int, ws_root: Path, samples: int, corpus: int) -> dict:
    import websockets
    ws_dir, _db = make_workspace(ws_root, "a", corpus)
    server = Server(ws_dir, port, tts_delay_ms=0, mock_reply="")
    rows: list[dict] = []
    try:
        server.wait_health()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws",
                                      max_size=16 * 1024 * 1024) as conn:
            # ready 是连接级事件，等它落地再开始计时
            hello = json.loads(await conn.recv())
            assert hello.get("type") == "ready", hello
            for i in range(samples):
                rows.append(await run_turn(conn, QUERY_HIT))
                await asyncio.sleep(0.15)  # 让 done 后的后台任务（记忆抽取等）落地
    finally:
        server.stop()
    spans = {key: stats([r["first"][key] * 1000 for r in rows if key in r["first"]])
             for key in ("asr", "thinking", "text_delta", "speech_plan", "audio", "done")}
    return {
        "query": QUERY_HIT, "samples": samples, "corpus": corpus,
        "spans_ms": spans,
        "first_audio_ms": spans["audio"],
        "server_reported_done_latency_ms": stats(
            [r["done_latency_ms"] for r in rows if r["done_latency_ms"] is not None]),
        "audio_chunks_per_turn": stats([r["audio_chunks"] for r in rows]),
        "audio_bytes_per_turn": stats([r["audio_bytes"] for r in rows]),
        "raw": rows,
    }


# --------------------------------------------------------------------------
# 指标 B：打断响应（barge_in -> playback_stopped）
# --------------------------------------------------------------------------

async def bench_barge_in(port: int, ws_root: Path, samples: int, corpus: int) -> dict:
    import websockets
    ws_dir, _db = make_workspace(ws_root, "b", corpus)
    server = Server(ws_dir, port, tts_delay_ms=250, mock_reply=BARGE_REPLY)
    rows: list[dict] = []
    try:
        server.wait_health()
        async with websockets.connect(f"ws://127.0.0.1:{port}/ws",
                                      max_size=16 * 1024 * 1024) as conn:
            hello = json.loads(await conn.recv())
            assert hello.get("type") == "ready", hello
            for i in range(samples):
                # 首个 audio 一到立刻 barge；若 turn 已先收轮（mock 链路太快），
                # 记为 miss——这本身就是一条链路观察。
                rows.append(await run_turn(conn, QUERY_HIT,
                                           barge_after_first_audio=True, drain_ms=300))
                await asyncio.sleep(0.15)
    finally:
        server.stop()
    hits = [r for r in rows if r["barge_to_playback_stopped_ms"] is not None]
    misses = [r for r in rows if r["barge_to_playback_stopped_ms"] is None]
    violations = [v for r in rows for v in r["violations"]]
    return {
        "query": QUERY_HIT, "samples": samples, "corpus": corpus,
        "tts_stub_delay_ms": 250,
        "hit_rate": f"{len(hits)}/{len(rows)}",
        "barge_to_interrupted_ms": stats(
            [r["barge_to_interrupted_ms"] for r in hits
             if r["barge_to_interrupted_ms"] is not None]),
        "barge_to_playback_stopped_ms": stats(
            [r["barge_to_playback_stopped_ms"] for r in hits]),
        "misses_turn_completed_before_barge": len(misses),
        "post_barge_audio_violations": len(violations),
        "raw": rows,
    }


# --------------------------------------------------------------------------
# 指标 C：记忆检索耗时（进程内，三档置信度装配路径）
# --------------------------------------------------------------------------

def bench_memory_retrieval(cold_samples: int, warm_samples: int, corpus_sizes: list[int]) -> dict:
    from server.memory.store import MemoryStore
    from server.memory.retriever import MemoryRetriever

    out: dict = {"corpus_sizes": corpus_sizes, "buckets": {}}
    for size in corpus_sizes:
        buckets: dict = {}

        # 冷：每样本全新 store（含 TF-IDF 首次拟合），计时只包 build_context
        cold = []
        for _ in range(cold_samples):
            store = MemoryStore(":memory:")
            seed_corpus(store, size)
            retriever = MemoryRetriever(store)
            t0 = time.perf_counter()
            retriever.build_context(QUERY_HIT, mark_used=False)
            cold.append((time.perf_counter() - t0) * 1000)
            store._conn.close()
        buckets["cold_index_rebuild"] = stats(cold)

        # 热：索引已缓存，轮换查询防止意外缓存；与生产调用同形（mark_used=False）
        store = MemoryStore(":memory:")
        seed_corpus(store, size)
        retriever = MemoryRetriever(store)
        retriever.build_context(QUERY_HIT, mark_used=False)  # 预热索引
        warm_queries = [QUERY_HIT, TIER_ANCHORS["inferred"][2],
                        TIER_ANCHORS["uncertain"][2], QUERY_MISS] * (warm_samples // 4 + 1)
        warm = []
        for q in warm_queries[:warm_samples]:
            t0 = time.perf_counter()
            retriever.build_context(q, mark_used=False)
            warm.append((time.perf_counter() - t0) * 1000)
        buckets["warm_cached"] = stats(warm)
        store._conn.close()

        # 分档命中：断言命中的确实是目标档（可解释），再计分布
        tier_stats: dict = {}
        for tier, (_key, _value, query) in TIER_ANCHORS.items():
            store = MemoryStore(":memory:")
            seed_corpus(store, size)
            retriever = MemoryRetriever(store)
            hits, times = [], []
            for _ in range(30):
                t0 = time.perf_counter()
                block, used, found = retriever.build_context(query, mark_used=False)
                times.append((time.perf_counter() - t0) * 1000)
                # JSON 记录里带 source 字段：命中的确实是目标档才算数
                if found and f'"source":"{tier}"' in block:
                    hits.append(True)
            tier_stats[tier] = {**stats(times), "hit": f"{sum(hits)}/{len(times)}"}
            store._conn.close()
        buckets["per_tier"] = tier_stats

        # 无命中：负向约束路径（检索为空 -> 动态提示词显式注入"没找到"）
        store = MemoryStore(":memory:")
        seed_corpus(store, size)
        retriever = MemoryRetriever(store)
        retriever.build_context(QUERY_HIT, mark_used=False)
        miss, miss_found = [], 0
        for _ in range(30):
            t0 = time.perf_counter()
            _block, _used, found = retriever.build_context(QUERY_MISS, mark_used=False)
            miss.append((time.perf_counter() - t0) * 1000)
            miss_found += 1 if found else 0
        if miss_found:  # ASCII 查询撞出命中 = 语料/查询被污染，数字不可用
            raise RuntimeError(f"miss query unexpectedly matched {miss_found}/30")
        buckets["no_hit_negative_path"] = {**stats(miss), "found_flag_true": 0}
        store._conn.close()
        out["buckets"][f"corpus_{size}"] = buckets
    return out


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------

async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--only", default="a,b,c", help="逗号分隔：a,b,c")
    parser.add_argument("--samples-a", type=int, default=40)
    parser.add_argument("--samples-b", type=int, default=30)
    parser.add_argument("--samples-c-cold", type=int, default=8)
    parser.add_argument("--samples-c-warm", type=int, default=60)
    parser.add_argument("--corpus", type=int, default=50,
                        help="指标 A/B 的记忆库规模（条）")
    parser.add_argument("--corpus-c", default="50,300",
                        help="指标 C 的语料规模列表（逗号分隔）")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--keep", action="store_true", help="保留 tmp 工作区")
    args = parser.parse_args()
    if args.port in FORBIDDEN_PORTS:
        print(f"REFUSED: port {args.port} 是用户端口（8000/8010），基准不得使用", file=sys.stderr)
        return 2
    only = {x.strip() for x in args.only.split(",")}

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    ws_root = ROOT / "tmp" / f"bench-voice-loop-{stamp}"
    ws_root.mkdir(parents=True, exist_ok=True)

    try:
        head = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                              cwd=str(ROOT), capture_output=True, text=True,
                              check=True).stdout.strip()
    except subprocess.CalledProcessError:  # pragma: no cover
        head = "unknown"

    report: dict = {
        "head": head, "date": datetime.now().astimezone().isoformat(timespec="seconds"),
        "port": args.port, "python": sys.version.split()[0],
        "mode": "Mock LLM（无 Key offline）+ 离线 WAV TTS 桩（bench_entry，生产文件零改动）",
        "machine_note": "单机本机回环；数字只代表链路开销，不代表真实供应商延迟",
    }
    if "a" in only:
        print(f"\n== 指标 A 快环首字延迟（text -> first audio），n={args.samples_a} =="
              f" corpus={args.corpus} port={args.port}")
        report["A_first_audio"] = await bench_first_audio(
            args.port, ws_root, args.samples_a, args.corpus)
        a = report["A_first_audio"]["first_audio_ms"]
        print(f"   text->audio1: p50={a['p50']}ms p90={a['p90']}ms p95={a['p95']}ms "
              f"max={a['max']}ms (n={a['n']})")
    if "b" in only:
        print(f"\n== 指标 B 打断响应（barge_in -> playback_stopped），n={args.samples_b} =="
              f" TTS桩=250ms/块 port={args.port}")
        report["B_barge_in"] = await bench_barge_in(
            args.port, ws_root, args.samples_b, args.corpus)
        b = report["B_barge_in"]
        print(f"   命中率={b['hit_rate']} barge->interrupted={b['barge_to_interrupted_ms']}"
              f" barge->playback_stopped={b['barge_to_playback_stopped_ms']}")
        print(f"   miss（turn 先收轮）={b['misses_turn_completed_before_barge']}"
              f" 打断后旧轮 audio 违规={b['post_barge_audio_violations']}")
    if "c" in only:
        sizes = [int(x) for x in args.corpus_c.split(",")]
        print(f"\n== 指标 C 记忆检索耗时（进程内 build_context），cold n={args.samples_c_cold}"
              f" warm n={args.samples_c_warm} corpus={sizes}")
        # 进程内 import 前先钉住 DB_PATH，防误碰真实库
        os.environ.setdefault("DB_PATH", str(ws_root / "inproc.db"))
        report["C_memory_retrieval"] = bench_memory_retrieval(
            args.samples_c_cold, args.samples_c_warm, sizes)
        for name, bucket in report["C_memory_retrieval"]["buckets"].items():
            for key, value in bucket.items():
                rows = (value.items() if key == "per_tier"
                        and isinstance(value, dict) else [(key, value)])
                for sub, entry in rows:
                    if isinstance(entry, dict) and "p50" in entry:
                        label = f"{name}.{key}.{sub}" if sub != key else f"{name}.{key}"
                        extra = f" hit={entry['hit']}" if "hit" in entry else ""
                        print(f"   {label}: p50={entry['p50']}ms p90={entry['p90']}ms "
                              f"max={entry['max']}ms n={entry['n']}{extra}")

    result_path = ws_root / "result.json"
    result_path.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                           encoding="utf-8")
    print(f"\nJSON -> {result_path}")
    if not args.keep:
        # server.memory.store 的模块级单例在 import 后持有 DB 连接：
        # 不去关它（进程马上退出），清不掉的文件只告警，不让清理炸掉报告。
        for child in ws_root.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                elif child.suffix in (".db", ".db-journal", ".py"):
                    child.unlink(missing_ok=True)
            except OSError as exc:
                print(f"   (清理跳过 {child.name}: {exc})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
