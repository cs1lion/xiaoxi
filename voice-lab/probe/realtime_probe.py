#!/usr/bin/env python3
"""
voice-lab 一次性实测探针 —— StepAudio 3 Realtime（throwaway，允许脏）

三问：
  --backchannel    Q1 服务端事件里能否区分「嗯嗯/对」(继续) 与「等等/停」(停)？含延迟
  --text-injection Q2 能否绕过模型生成，让服务端只念给定文本？两法并测：
                     A) conversation.item.create(assistant,input_text)+response.create（文档疑不支持）
                     B) 开场白技巧：instructions「原样输出这句」+response.create（软约束）
  --alignment      Q3 打断后服务端有无「实际播到第 N ms」；transcript 有无字级时间戳；
                   另：我方主动发 conversation.item.truncate（阶跃文档 0 次）看服务端接不接

隔离硬约束：
  - 零 import server/web/config。
  - 凭据只读 voice-lab/.env（已 gitignore）。主 LLM_API_KEY 由主人 2026-10-24 明示授权
    复制进该 .env（限免期），本脚本仍绝不读根 .env。
  - 不带 --live 绝不连网。MAX_SESSIONS=6 写死，触顶即停。
  - 原始输出只落 voice-lab/outputs/（gitignore）。录音为 TTS 合成，非真实麦克风原音。

运行：
  .\\.venv\\Scripts\\python.exe -I voice-lab/probe/realtime_probe.py --selftest
  .\\.venv\\Scripts\\python.exe -I voice-lab/probe/realtime_probe.py --live --text-injection
  .\\.venv\\Scripts\\python.exe -I voice-lab/probe/realtime_probe.py --live --backchannel
  .\\.venv\\Scripts\\python.exe -I voice-lab/probe/realtime_probe.py --live --alignment
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import sys
import time
import uuid
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
OUTPUTS = LAB / "outputs"
ENV_FILE = LAB / ".env"

MAX_SESSIONS = 6          # 写死上限；触顶即停。2026-10-24 从 3 升到 6：模型限免（smoke 实测非 preview 档 402），主人授权复跑坐实
MAX_SESSION_S = 90        # 单会话最长驻留（秒）
DEFAULT_MODEL = "stepaudio-3-realtime-preview"
ENDPOINT = "wss://api.stepfun.com/v1/realtime"   # 3.0 preview 走不带 plan 的 URL（实测握手OK；step_plan 对该模型 404）
RATE = 24_000

_sessions_opened = 0


def log(*a):
    print(*a, flush=True)


def load_env():
    if not ENV_FILE.exists():
        return None
    kv = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if s and not s.startswith("#") and "=" in s:
            k, v = s.split("=", 1)
            kv[k.strip()] = v.strip().strip('"').strip("'")
    key = kv.get("STEP_REALTIME_API_KEY", "")
    if not key:
        return None
    return {"key": key, "model": kv.get("STEP_REALTIME_MODEL", DEFAULT_MODEL),
            "endpoint": kv.get("STEP_REALTIME_ENDPOINT", ENDPOINT)}


def blocked_reason():
    return ("BLOCKED: voice-lab/.env 缺 STEP_REALTIME_API_KEY。本脚本绝不读根 .env；"
            "须由人自行把 key 放进 voice-lab/.env（gitignore）。")


# ---------------- 判定器（离线可自测） ----------------

CLASSIFY_HINTS = ("backchannel", "interrupt", "intent", "category", "confidence", "is_", "type_")


def observe_speech_started(evt):
    """Q1 核心判定：speech_started 是否携带『打断 vs 附和』的可观测信号。"""
    fields = sorted(evt.keys())
    hits = [k for k in fields if any(t in k.lower() for t in CLASSIFY_HINTS)]
    return {"fields_present": fields, "classification_fields": hits,
            "verdict": "协议层可判定" if hits else "协议层不可观测"}


def observe_offset(evt):
    """Q3：事件里有没有『播到第 N ms』。"""
    offset = {k: evt[k] for k in ("audio_end_ms", "played_ms", "offset_ms") if k in evt}
    sd = evt.get("status_details")
    if not offset and isinstance(sd, dict):
        offset = {f"status_details.{k}": v for k, v in sd.items()
                  if k in ("audio_end_ms", "played_ms", "reason")}
    return offset


def has_word_timestamps(evt):
    """在整棵事件树里找『字/词级时间戳』形态：list[dict] 且带 start/end 类字段、键名含 words。"""
    found = []
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k.lower() in ("words", "word_timings", "timestamps") and isinstance(v, list) and v \
                        and isinstance(v[0], dict) and any(
                            kk in v[0] for kk in ("start", "end", "start_ms", "t0")):
                    found.append({k: v[:2]})
                walk(v)
        elif isinstance(node, list):
            for x in node:
                walk(x)
    walk(evt)
    return found if found else False


def verbatim(transcript, target):
    n = lambda s: "".join((s or "").split())
    return n(transcript) == n(target)


# ---------------- 音频 ----------------

def pcm_frames(path: Path, chunk_ms=25):
    raw = path.read_bytes()
    if shutil.which("ffmpeg") and not path.name.endswith(".native24k.wav"):
        p = subprocess.run(["ffmpeg", "-v", "error", "-i", str(path), "-f", "s16le",
                            "-acodec", "pcm_s16le", "-ar", str(RATE), "-ac", "1", "-"],
                           capture_output=True)
        if p.returncode != 0:
            raise RuntimeError("ffmpeg fail: " + p.stderr.decode(errors="ignore")[:200])
        pcm = p.stdout
    else:
        with wave.open(str(path), "rb") as w:
            pcm = w.readframes(w.getnframes())
    bps = int(RATE * chunk_ms / 1000) * 2
    return [(i, base64.b64encode(pcm[o:o + bps]).decode()) for i, o in
            enumerate(range(0, len(pcm), bps))]


SILENCE25 = base64.b64encode(b"\x00\x00" * (int(RATE * 25 / 1000))).decode()


# ---------------- 会话驱动 ----------------

async def recv_until(ws, sink, transcript, stop_types, deadline, extra_ms=3000):
    while time.monotonic() < deadline:
        try:
            evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=max(0.5, deadline - time.monotonic())))
        except asyncio.TimeoutError:
            break
        except Exception:
            break
        sink.append((round(time.monotonic(), 3), evt))
        if transcript is not None and evt.get("type") == "response.audio_transcript.delta":
            transcript.append(evt.get("delta", ""))
        if evt.get("type") in stop_types and len(sink) > 1:
            await asyncio.sleep(extra_ms / 1000)
            return
        if evt.get("type") == "error":
            await asyncio.sleep(0.3)


async def run_session(cfg, kind, rec_dir, target_text):
    global _sessions_opened
    if _sessions_opened >= MAX_SESSIONS:
        log(f"[cap] 已达 MAX_SESSIONS={MAX_SESSIONS}，停。已用 {_sessions_opened}")
        return None
    _sessions_opened += 1
    import websockets
    url = f"{cfg['endpoint']}?model={cfg['model']}"
    log(f"[session {_sessions_opened}/{MAX_SESSIONS}] {kind} @ {cfg['endpoint']} model={cfg['model']}")
    t_start = time.monotonic()
    events = []          # [(wall, evt)]
    transcript = []      # Q2 用
    verdict = None

    try:
      async with websockets.connect(url, additional_headers={"Authorization": f"Bearer {cfg['key']}"},
                                    max_size=16 * 1024 * 1024) as ws:
        async def send(o):
            try:
                await ws.send(json.dumps(o))
            except Exception as se:
                events.append((round(time.monotonic(), 3),
                               {"type": "__send_failed__", "detail": str(se), "sent": o.get("type")}))
                raise

        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        events.append((round(time.monotonic(), 3), first))
        assert first.get("type") == "session.created", f"首事件异常: {first}"

        instr = None
        if kind == "text-injection":
            instr = (f"请把下面这句话原样、逐字、不加任何修改地用语音说出来，不要说别的：\n「{target_text}」")
        elif kind == "backchannel":
            instr = ("请用中文连续独白 15 秒以上：详细介绍周末去公园游玩可以做的五六件事，"
                     "一件说完直接说下一件，全程不要停顿、不要问用户、不要等回复。")
        await send({"type": "session.update", "session": {
            "modalities": ["text", "audio"], "instructions": instr,
            "turn_detection": {"type": "server_vad", "silence_duration_ms": 400,
                               "prefix_padding_ms": 300, "energy_awakeness_threshold": 2500},
            "input_audio_format": "pcm16", "output_audio_format": "pcm16"}})

        deadline = t_start + MAX_SESSION_S

        if kind == "text-injection":
            # 方法B（开场白技巧）：instructions 已含「原样输出」，另补一条 user 消息触发
            # （首跑发现：空历史 + response.create → 服务端 400，故补 user 轮；这本身是实测事实）
            await send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "请开始。"}]}})
            await send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})
            await recv_until(ws, events, transcript, {"response.done"}, deadline, extra_ms=1200)
            tB = "".join(transcript)
            # 方法A：注入 assistant 文本项，再触发 response（预期被服务端拒）
            transcript.clear()
            await send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "assistant",
                "content": [{"type": "input_text", "text": target_text}]}})
            await send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})
            await recv_until(ws, events, transcript, {"response.done"}, deadline, extra_ms=1200)
            tA = "".join(transcript)
            errs = [e for _, e in events if e.get("type") == "error"]
            verdict = {
                "target": target_text,
                "methodB_instructions_parrot": {"got": tB, "verbatim": verbatim(tB, target_text)},
                "methodA_assistant_item": {"got": tA, "verbatim": verbatim(tA, target_text)},
                "methodA_server_errors": [e.get("error", {}) for e in errs][:2],
            }
        elif kind == "backchannel":
            # 长独白写进 instructions，user 种子只负责「触发开口」；
            # 空历史裸 response.create 会得到空转写/0 音频（Q2 首跑实测）。
            # 打断场景要求助手正在说话时喂入 → 先并发开收，等首个足量 audio.delta 再喂。
            await send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "开始吧。"}]}})
            await send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})
            clips = [(w.name, pcm_frames(w)) for w in sorted(Path(rec_dir).glob("*.wav"))]
            reader = asyncio.create_task(pump_recv(ws, events))
            spoken = False
            for _ in range(80):    # 最多等 8s
                await asyncio.sleep(0.1)
                if any(e.get("type") == "response.audio.delta" and
                       len(base64.b64decode(e.get("delta", ""))) >= 3200 for _, e in events):
                    spoken = True
                    break
            if not spoken:
                log("[warn] Q1 助手没吐音频——协议层字段证据仍有效，行为层判『场景未成立』")
            feed_starts = {}
            if spoken:
                for name, frames in clips:
                    feed_starts[name] = round(time.monotonic(), 3)
                    for _, b64 in frames:
                        await send({"type": "input_audio_buffer.append", "audio": b64})
                        await asyncio.sleep(0.025)
                    for _ in range(60):    # 条间 1.5s 静音：上一轮 0.5s 出现 VAD 闩锁
                                           # （整场只有一个 speech_started、0 个 speech_stopped），
                                           # 拉开间隔让每条录音有机会独立成段
                        await send({"type": "input_audio_buffer.append", "audio": SILENCE25})
                        await asyncio.sleep(0.025)
            await asyncio.sleep(3.0)
            reader.cancel()
            try:
                await reader
            except asyncio.CancelledError:
                pass
            verdict = analyze_backchannel(events, feed_starts, clips)
            verdict["assistant_actually_spoke_before_feed"] = spoken
        else:  # alignment
            await send({"type": "conversation.item.create", "item": {
                "type": "message", "role": "user",
                "content": [{"type": "input_text", "text": "用中文说一段二十秒左右的话，讲讲周末安排。"}]}})
            await send({"type": "response.create", "response": {"modalities": ["audio", "text"]}})
            t_first_audio = None
            audio_bytes = 0
            item_id = None
            transport_dead = False
            while time.monotonic() < deadline and (t_first_audio is None or time.monotonic() - t_first_audio < 2.5):
                try:
                    evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=8))
                except asyncio.TimeoutError:
                    break
                except Exception as ce:
                    transport_dead = True
                    events.append((round(time.monotonic(), 3),
                                   {"type": "__transport_closed__", "detail": str(ce)}))
                    break
                wall = round(time.monotonic(), 3)
                events.append((wall, evt))
                if evt.get("type") == "response.audio.delta":
                    if t_first_audio is None:
                        t_first_audio = wall
                    audio_bytes += len(base64.b64decode(evt.get("delta", "")))
                elif evt.get("type") in ("response.output_item.added", "conversation.item.created"):
                    item_id = (evt.get("item") or {}).get("id") or evt.get("item_id") or item_id
            played_ms_est = round(audio_bytes / (RATE * 2) * 1000, 1)
            t_cancel = round(time.monotonic(), 3)
            post_cancel = "n/a（传输已断）"
            if not transport_dead:
                try:
                    await send({"type": "response.cancel"})
                    n_before = len(events)
                    await recv_until(ws, events, transcript, {"response.done", "response.cancelled"},
                                     deadline, extra_ms=600)
                    post_cancel = [e.get("type") for _, e in events[n_before:]]
                except Exception as ce:
                    transport_dead = True
                    post_cancel = f"cancel 发送/接收中传输断：{ce}"
            # 追加实验：我方主动发阶跃文档 0 次出现的 truncate，看服务端接不接
            trunc_resp = "（未发：会话已断或无 item_id）"
            if item_id and not transport_dead:
                try:
                    await send({"type": "conversation.item.truncate",
                                "item_id": item_id, "content_index": 0, "audio_end_ms": 2000})
                    probe_deadline = time.monotonic() + 3
                    while time.monotonic() < probe_deadline:
                        try:
                            evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=1))
                        except asyncio.TimeoutError:
                            break
                        except Exception:
                            trunc_resp = "truncate 发送后传输断"
                            break
                        events.append((round(time.monotonic(), 3), evt))
                        if evt.get("type") in ("error", "conversation.item.truncated"):
                            trunc_resp = evt
                            break
                except Exception as ce:
                    trunc_resp = f"truncate 发送失败：{ce}"
            verdict = analyze_alignment(events, played_ms_est, t_cancel, trunc_resp)
            verdict["post_cancel_event_types"] = post_cancel
            verdict["transport_abnormal_close_during_cancel"] = transport_dead

        await ws.close()
    except Exception as se:
        events.append((round(time.monotonic(), 3), {"type": "__run_died__", "detail": str(se)}))
        if verdict is None:
            verdict = {"run_aborted": str(se),
                       "hint": "会话体中途异常退出；看 events_types 里 __transport_closed__/"
                               "__send_failed__ 的相对位置判断死在哪一步"}

    out = {"kind": kind, "model": cfg["model"], "endpoint": cfg["endpoint"],
           "ran_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "session_s": round(time.monotonic() - t_start, 1),
           "events_types": [e["type"] for _, e in events],
           "verdict": verdict}
    OUTPUTS.mkdir(exist_ok=True)
    fp = OUTPUTS / f"{kind}-{uuid.uuid4().hex[:8]}.json"
    fp.write_text(json.dumps({"summary": out, "events": [[w, e] for w, e in events]},
                             ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    log(f"[写出] {fp.relative_to(LAB)}  ({len(events)} events)")
    log(json.dumps(verdict, ensure_ascii=False, indent=2, default=str))
    return out


async def recv_and_capture(ws, events, transcript):
    try:
        while True:
            evt = json.loads(await ws.recv())
            events.append((round(time.monotonic(), 3), evt))
            if evt.get("type") == "response.audio_transcript.delta":
                transcript.append(evt.get("delta", ""))
    except Exception:
        pass


async def pump_recv(ws, events):
    """并发排空接收，把事件带时间戳入 events；被 cancel 即止。"""
    try:
        while True:
            evt = json.loads(await ws.recv())
            events.append((round(time.monotonic(), 3), evt))
    except (asyncio.CancelledError, Exception):
        return


def analyze_backchannel(events, feed_starts, clips):
    """每条录音按自己的喂入窗口归因 speech_started，看协议有无分类信号。"""
    starts = [(w, e) for w, e in events if e.get("type") == "input_audio_buffer.speech_started"]
    cancels = [(w, e) for w, e in events if e.get("type") in
               ("response.cancelled", "response.done")]
    new_resp = [(w, e) for w, e in events if e.get("type") == "response.created"]
    in_trans = {}
    for w, e in events:
        if e.get("type", "").endswith("input_audio_transcription.completed"):
            in_trans.setdefault(e.get("item_id", "?"), []).append(e.get("transcript", ""))
    audio_ts = [w for w, e in events if e.get("type") == "response.audio.delta"]
    names = [n for n, _ in clips]
    rows = []
    for idx, name in enumerate(names):
        f0 = feed_starts[name]
        f1 = feed_starts[names[idx + 1]] if idx + 1 < len(names) else f0 + 60
        win = [(w, e) for w, e in starts if f0 - 0.15 <= w < f1]
        match = win[0] if win else None
        # 助手当时是否正在发声（f0 前 2 秒内仍有 audio.delta 流出）
        speaking = any(f0 - 2.0 <= w <= f0 + 0.15 for w in audio_ts)
        rows.append({"clip": name,
                     "assistant_was_speaking": speaking,
                     "detected": match is not None,
                     "detect_latency_ms": round((match[0] - f0) * 1000, 1) if match else None,
                     "classification": observe_speech_started(match[1]) if match else None,
                     "n_speech_started_in_window": len(win),
                     "server_cancelled_current_response": any(f0 - 0.15 <= w < f1 for w, _ in cancels),
                     "server_started_new_response": any(f0 - 0.15 <= w < f1 for w, _ in new_resp)})
    fields_union = sorted({k for r in rows if r["classification"] for k in r["classification"]["fields_present"]})
    return {"per_clip": rows,
            "speech_started_fields_union": fields_union,
            "input_transcripts": in_trans,
            "summary": ("协议层可区分" if any(r["classification"] and "可判定" in r["classification"]["verdict"]
                                             for r in rows) else
                        "协议层不可区分：speech_started 不带打断/附和分类字段；行为面只能看服务端对每条的 react 是否不同")}


def analyze_alignment(events, played_ms_est, t_cancel, trunc_resp):
    types = [e.get("type") for _, e in events]
    rel = [e for _, e in events if e.get("type") in
           ("response.cancelled", "response.done", "response.audio.done",
            "response.audio_transcript.done", "response.output_item.done",
            "conversation.item.truncated")]
    offsets = [{"type": e.get("type"), "offset": observe_offset(e)} for e in rel]
    wt = [{"type": e.get("type"), "where": has_word_timestamps(e)} for e in rel
          if has_word_timestamps(e)]
    # 全事件面再扫一遍（不只看 done 类，防漏）
    wt_all = sorted({e.get("type") for _, e in events if has_word_timestamps(e)})
    # text↔audio 交错：transcript 增量与 audio 增量是否同步到达（形象层想要「说到第 N 字」）
    tr_deltas = [(w, len(e.get("delta", ""))) for w, e in events
                 if e.get("type") == "response.audio_transcript.delta"]
    au_deltas = [(w, len(base64.b64decode(e.get("delta", ""))) // 2)
                 for w, e in events if e.get("type") == "response.audio.delta"]
    return {
        "event_types_seen": sorted(set(types)),
        "client_est_played_ms_at_cancel": played_ms_est,
        "server_offset_events": [o for o in offsets if o["offset"]],
        "server_offset_present": any(o["offset"] for o in offsets),
        "word_timestamps_on_done_events": wt,
        "word_timestamps_any_event": wt_all,
        "transcript_delta_count_at_cancel": sum(1 for w, _ in tr_deltas if w <= t_cancel),
        "audio_sample_count_at_cancel": sum(s for w, s in au_deltas if w <= t_cancel),
        "truncate_event_server_response": (trunc_resp if isinstance(trunc_resp, str)
                                           else trunc_resp.get("type")),
        "truncate_probe_detail": trunc_resp,
    }


# ---------------- 自测（离线） ----------------

def selftest():
    ok = True
    def check(n, c):
        nonlocal ok; ok = ok and c
        log(f"  [{'PASS' if c else 'FAIL'}] {n}")
    b = observe_speech_started({"type": "input_audio_buffer.speech_started",
                                "event_id": "e", "audio_start_ms": 1200, "item_id": "i"})
    check("Q1 无分类字段→不可观测", b["verdict"] == "协议层不可观测")
    b2 = observe_speech_started({"type": "input_audio_buffer.speech_started", "is_backchannel": False})
    check("Q1 有分类字段→可判定", b2["verdict"] == "协议层可判定")
    check("Q3 无偏移→{}", observe_offset({"type": "response.cancelled"}) == {})
    check("Q3 有 audio_end_ms→命中", observe_offset({"audio_end_ms": 900}) == {"audio_end_ms": 900})
    check("Q3 status_details.reason→命中",
          observe_offset({"status_details": {"reason": "interruption"}}) == {"status_details.reason": "interruption"})
    check("Q3 纯字符串 transcript 无字级时间戳",
          has_word_timestamps({"transcript": "你好", "type": "x.done"}) is False)
    check("Q3 嵌套 response.transcript.words→命中",
          bool(has_word_timestamps({"response": {"transcript": {"words": [{"word": "你", "start": 0.1}]}}})))
    check("Q3 output[].content.words→命中",
          bool(has_word_timestamps({"output": [{"content": [{"words": [{"w": "你", "start": 0}]}]}]})))
    check("Q2 逐字等", verbatim("今天 天气 不错", "今天天气不错"))
    check("Q2 改写不等", not verbatim("今天不错啦", "今天天气不错"))
    tmp = OUTPUTS / "_st.wav"; OUTPUTS.mkdir(exist_ok=True)
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * RATE)
    fr = pcm_frames(tmp)
    check("PCM 约40帧", 38 <= len(fr) <= 42)
    tmp.unlink(missing_ok=True)
    # 归因逻辑：两条窗口不串
    ev = [(10.0, {"type": "input_audio_buffer.speech_started", "audio_start_ms": 1}),
          (20.0, {"type": "input_audio_buffer.speech_started", "audio_start_ms": 2})]
    r = analyze_backchannel(ev, {"a.wav": 9.9, "b.wav": 19.9}, [("a.wav", []), ("b.wav", [])])
    check("归因按窗口：各命中自己那条",
          [x["detect_latency_ms"] for x in r["per_clip"]] == [100.0, 100.0])
    log(f"[selftest] {'全绿' if ok else '有FAIL'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--backchannel", action="store_true")
    ap.add_argument("--text-injection", action="store_true")
    ap.add_argument("--alignment", action="store_true")
    ap.add_argument("--recordings", default=str(OUTPUTS / "recordings"))
    ap.add_argument("--target-text", default="现在是下午三点，我在测试逐字播报能不能成立。")
    args = ap.parse_args()
    if args.selftest:
        sys.exit(selftest())
    modes = [m for m in ("backchannel", "text_injection", "alignment") if getattr(args, m)]
    if not modes:
        log("未选模式（--backchannel/--text-injection/--alignment/--selftest）"); sys.exit(2)
    if not args.live:
        log("[gate] 无 --live，不联网。"); sys.exit(3)
    cfg = load_env()
    if cfg is None:
        log(blocked_reason()); sys.exit(4)

    async def go():
        for m in modes:
            kind = m.replace("_", "-")
            if kind == "backchannel":
                rec = Path(args.recordings)
                if not rec.exists() or not list(rec.glob("*.wav")):
                    log(f"[gate] 缺录音 {rec}/*.wav，跳过 Q1"); continue
            await run_session(cfg, kind, args.recordings, args.target_text)
        log(f"[用量] 会话 {_sessions_opened}/{MAX_SESSIONS}；原始事件见 voice-lab/outputs/")
    asyncio.run(go())


if __name__ == "__main__":
    main()
