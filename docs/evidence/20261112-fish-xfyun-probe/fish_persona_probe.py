# -*- coding: utf-8 -*-
"""Fish Audio 与项目 persona 情绪表的映射可行性探测。

回答三个问题：
1. persona.yaml 的 instruction 是中文自然语言（"语气自然随意，像熟人聊天"），
   Fish S2 的方括号是否接受自然语言描述（不只固定 tag）？
2. 七档情绪各自合成，字节数是否不同（不同 = 情绪真的进了音频）？
3. 首字节延迟 vs 完结延迟（快环关心前者）。
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))

import httpx

KEY = os.environ.get("FISH_API_KEY", "")
BASE = "https://api.fish.audio"
HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

# persona.yaml 的七档 instruction，原样取
PERSONA = {
    "base": "语气自然随意，像熟人聊天，不要播音腔，不刻意表演",
    "调侃": "带点调侃和促狭，语速轻快，尾音上扬，但不是在生气",
    "得意": "有点小得意，藏不住的开心，语速稍快，一句就收住不要拖长",
    "认真": "语气沉稳认真，语速放慢一点，咬字清楚，像在认真跟你讲一件事",
    "收敛": "语气放轻放慢，带一点歉意，不辩解，不要委屈腔",
    "轻微不服": "略带不服气，语气有点冲，但不是发火，别吼",
    "变小气": "有点冷淡，不太想搭理的感觉，声音小一些，语速慢半拍",
}

LINE = "你回来啦。今天过得怎么样？"


def synth(text, **body):
    payload = {"text": text, "format": "mp3", "language": "zh-CN", **body}
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/tts", json=payload,
                   headers={**HDR, "model": "s2.1-pro-free"}, timeout=120)
    return r, (time.perf_counter() - t0) * 1000


def dur(mp3: bytes) -> float:
    import subprocess
    p = Path(os.environ["TEMP"]) / "fish_dur.mp3"
    p.write_bytes(mp3)
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=noprint_wrappers=1:nokey=1", str(p)],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    print("[A] persona instruction 直接放进方括号（自然语言，非固定 tag）")
    r, ms = synth(f"[{PERSONA['base']}]{LINE}")
    print(f"  括号内 = 中文 instruction -> HTTP {r.status_code} {len(r.content)}B {round(ms)}ms")
    if r.status_code != 200:
        print(f"  body: {r.text[:200]}")

    print("\n[B] 七档情绪各自合成，看字节/时长是否分化")
    rows = []
    for emo, instr in PERSONA.items():
        rr, m = synth(f"[{instr}]{LINE}")
        d = dur(rr.content) if rr.status_code == 200 else 0.0
        rows.append((emo, len(rr.content), round(d, 3), round(m)))
        print(f"  {emo:6s} HTTP {rr.status_code}  {len(rr.content):7d}B  {d:.3f}s  wall {round(m)}ms")
    uniq = len({r_[1] for r_ in rows})
    print(f"  不同字节数的档数: {uniq}/{len(rows)}  (全同 = 情绪没进音频)")

    print("\n[C] 对照：同一 instruction 重复两次，字节应一致（确定性检查）")
    a, _ = synth(f"[{PERSONA['认真']}]{LINE}")
    b, _ = synth(f"[{PERSONA['认真']}]{LINE}")
    print(f"  两次字节: {len(a.content)} vs {len(b.content)}  相同={a.content == b.content}")

    print("\n[D] 无括号 vs 有括号：确认括号确实改变输出")
    n, _ = synth(LINE)
    y, _ = synth(f"[{PERSONA['变小气']}]{LINE}")
    print(f"  无括号 {len(n.content)}B / 有括号 {len(y.content)}B  不同={n.content != y.content}")

    print("\n[E] 首字节延迟（流式，逐 chunk 到达时刻）")
    payload = {"text": LINE, "format": "mp3", "language": "zh-CN",
               "chunk_length": 200, "latency": "balanced"}
    t0 = time.perf_counter()
    first = None
    chunks = 0
    with httpx.stream("POST", f"{BASE}/v1/tts/stream/with-timestamp", json=payload,
                      headers={**HDR, "model": "s2.1-pro-free"}, timeout=120) as resp:
        for line in resp.iter_lines():
            if not line.startswith("data: "):
                continue
            if first is None:
                first = (time.perf_counter() - t0) * 1000
            chunks += 1
    total = (time.perf_counter() - t0) * 1000
    print(f"  首字节 {round(first or 0)}ms / 完结 {round(total)}ms / {chunks} 个 SSE 事件")

    print("\n[F] 长文本上限（stepaudio 只给 1000 字符）")
    for n_chars in [500, 1000, 3000]:
        rr, m = synth("嗯" * n_chars)
        print(f"  {n_chars} 字符 -> HTTP {rr.status_code} {len(rr.content)}B {round(m)}ms")

    out = Path(os.environ["TEMP"]) / "fish_persona_probe.json"
    out.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
