# -*- coding: utf-8 -*-
"""Fish Audio 在项目真实分片尺寸下的延迟画像。

背景：persona 探测发现 Fish 对长文本极慢（500 字符 65s、1000 字符 194s）。
但项目 config/pacing.yaml 的 micro_chunk 是 target_chars=26 / max_chars=60，
每轮 TTS 调用只有 20~60 字。本脚本按真实分片尺寸测延迟，
并对比 latency 三档（low / normal / balanced）与 chunk_length。
"""
from __future__ import annotations

import json
import os
import statistics
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

URL = "https://api.fish.audio/v1/tts"
H = {"Authorization": f"Bearer {os.environ.get('FISH_API_KEY', '')}",
     "Content-Type": "application/json"}
MODEL = "s2.1-pro-free"

# 项目真实分片尺寸的句子
CHUNKS = {
    "14字": "你回来啦。今天过得怎么样？",
    "26字": "这个我还真没细想过，不过听起来挺有意思的。",
    "45字": "刚加班到现在才弄完那个破项目，累得我都不想说话了，你说这班上得值吗。",
    "60字": "刚才你说的那件事我记着呢，咱们要不要趁现在有空把它从头捋一遍，看看还差哪几步。",
}


def one(text, *, latency="balanced", chunk_length=200, model=MODEL, timeout=180):
    body = {"text": text, "format": "mp3", "language": "zh-CN",
            "latency": latency, "chunk_length": chunk_length,
            "prosody": {"speed": 1, "volume": 0, "normalize_loudness": True}}
    t0 = time.perf_counter()
    r = httpx.post(URL, json=body, headers={**H, "model": model}, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    return r, ms


def main() -> int:
    print("[A] 分片尺寸 × latency 档（各 3 次，看中位数与波动）")
    rows = []
    for label, text in CHUNKS.items():
        for lat in ["low", "balanced", "normal"]:
            times, sizes = [], []
            for _ in range(3):
                r, ms = one(text, latency=lat)
                times.append(ms)
                sizes.append(len(r.content))
            med = statistics.median(times)
            print(f"  {label:6s} latency={lat:9s} 中位 {med:7.0f}ms  "
                  f"范围 {min(times):.0f}~{max(times):.0f}ms  {sizes[0]}B")
            rows.append({"chunk": label, "latency": lat, "median_ms": round(med),
                         "min_ms": round(min(times)), "max_ms": round(max(times)),
                         "bytes": sizes[0]})

    print("\n[B] chunk_length 对延迟的影响（60 字句子）")
    text = CHUNKS["60字"]
    for cl in [100, 200, 300]:
        times = []
        for _ in range(3):
            r, ms = one(text, latency="balanced", chunk_length=cl)
            times.append(ms)
        print(f"  chunk_length={cl:4d}  中位 {statistics.median(times):7.0f}ms")
        rows.append({"chunk": "60字", "chunk_length": cl,
                     "median_ms": round(statistics.median(times))})

    print("\n[C] 情绪标签是否增加延迟（60 字 + 括号）")
    text60 = CHUNKS["60字"]
    for tag in [None, "[soft tone]", "[whispering]"]:
        times = []
        for _ in range(3):
            payload = f"[{tag}] {text60}" if tag else text60
            r, ms = one(payload, latency="balanced")
            times.append(ms)
        print(f"  {str(tag):15s} 中位 {statistics.median(times):7.0f}ms")
        rows.append({"tag": tag, "median_ms": round(statistics.median(times))})

    print("\n[D] 「变小气」与「轻微不服」字节数相同的复核")
    personas = {
        "轻微不服": "略带不服气，语气有点冲，但不是发火，别吼",
        "变小气": "有点冷淡，不太想搭理的感觉，声音小一些，语速慢半拍",
        "收敛": "语气放轻放慢，带一点歉意，不辩解，不要委屈腔",
    }
    for emo, instr in personas.items():
        times = []
        for _ in range(2):
            r, ms = one(f"[{instr}]{CHUNKS['26字']}")
            times.append(len(r.content))
        print(f"  {emo:6s} 两次字节 {times}  相同={times[0] == times[1]}")
        rows.append({"persona": emo, "bytes_runs": times})

    print("\n[E] 延迟档位建议的对照读数")
    for lat in ["low", "balanced", "normal"]:
        times = []
        for _ in range(5):
            r, ms = one(CHUNKS["26字"], latency=lat)
            times.append(ms)
        print(f"  {lat:9s} 5 次: {[round(t) for t in times]}  中位 {statistics.median(times):.0f}ms")
        rows.append({"latency": lat, "runs_ms": [round(t) for t in times],
                     "median_ms": round(statistics.median(times))})

    out = os.path.join(os.environ["TEMP"], "fish_latency_profile.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
