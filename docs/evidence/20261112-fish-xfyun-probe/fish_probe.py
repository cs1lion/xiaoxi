# -*- coding: utf-8 -*-
"""Fish Audio 能力探测（只读，不写仓库数据）。

用途：在决定「接哪个 TTS 做闭环」之前，把 Fish Audio 的实际能力测出来，
而不是照文档转述。每个数字都是本机实跑。
"""
from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

ROOT = Path(__file__).resolve().parents[1]
KEY = os.environ.get("FISH_API_KEY", "")
BASE = "https://api.fish.audio"
HDR = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

RESULTS = []


def record(name, value, note=""):
    RESULTS.append({"name": name, "value": value, "note": note})
    print(f"  {name}: {value}  {note}")


def tts(text, *, model="s2.1-pro-free", **body):
    import httpx
    payload = {"text": text, "format": "mp3", **body}
    h = {**HDR, "model": model}
    t0 = time.perf_counter()
    r = httpx.post(f"{BASE}/v1/tts", json=payload, headers=h, timeout=120)
    dt = (time.perf_counter() - t0) * 1000
    return r, dt


def main() -> int:
    if not KEY:
        print("FISH_API_KEY 未设置")
        return 2

    print("[1] 基础合成 + 中文 + 延迟")
    r, dt = tts("你回来啦。今天过得怎么样？", language="zh-CN")
    record("status", r.status_code)
    record("bytes", len(r.content))
    record("wall_ms", round(dt))
    record("content_type", r.headers.get("content-type"))
    record("magic", r.content[:3].hex())

    print("\n[2] 模型档位对比（同一句）")
    for model in ["s2.1-pro-free", "s2.1-pro", "drama-3", "s1"]:
        try:
            rr, d = tts("你好呀。", model=model)
            record(model, f"HTTP {rr.status_code} {len(rr.content)}B {round(d)}ms")
        except Exception as exc:
            record(model, f"ERR {type(exc).__name__}")

    print("\n[3] 情绪标签（S2 方括号）")
    for tag in ["[happy]", "[soft tone]", "[whispering]", "[sarcastic]", "[angry]"]:
        try:
            rr, d = tts(f"{tag}你回来啦。今天过得怎么样？", language="zh-CN")
            record(tag, f"HTTP {rr.status_code} {len(rr.content)}B")
        except Exception as exc:
            record(tag, f"ERR {type(exc).__name__}")

    print("\n[4] prosody speed 生效性（同文本不同 speed，看时长差）")
    durations = {}
    for speed in [0.8, 1.0, 1.3]:
        rr, d = tts("今天过得怎么样？", language="zh-CN",
                    prosody={"speed": speed, "volume": 0, "normalize_loudness": True})
        mp3 = Path(os.environ["TEMP"]) / f"fish_speed_{speed}.mp3"
        mp3.write_bytes(rr.content)
        dur = probe_duration(mp3)
        durations[speed] = dur
        record(f"speed={speed}", f"{dur:.3f}s audio, wall {round(d)}ms")
    if len(durations) == 3:
        base = durations[1.0]
        record("speed_0.8_ratio", round(durations[0.8] / base, 3) if base else "n/a")
        record("speed_1.3_ratio", round(durations[1.3] / base, 3) if base else "n/a")

    print("\n[5] 逐字对齐（stream/with-timestamp）")
    try:
        import httpx
        payload = {"text": "你回来啦。今天过得怎么样？", "format": "mp3",
                   "language": "zh-CN", "chunk_length": 200, "latency": "balanced"}
        t0 = time.perf_counter()
        with httpx.stream("POST", f"{BASE}/v1/tts/stream/with-timestamp",
                          json=payload, headers={**HDR, "model": "s2.1-pro-free"},
                          timeout=120) as resp:
            status = resp.status_code
            segs = []
            total_b64 = 0
            for line in resp.iter_lines():
                if not line.startswith("data: "):
                    continue
                ev = json.loads(line[6:])
                total_b64 += len(ev.get("audio_base64", ""))
                for s in (ev.get("alignment") or {}).get("segments", []):
                    segs.append(s)
        dt = (time.perf_counter() - t0) * 1000
        record("status", status)
        record("segments_count", len(segs))
        record("segments_sample", json.dumps(segs[:6], ensure_ascii=False))
        record("stream_wall_ms", round(dt))
        record("decoded_audio_bytes", total_b64 * 3 // 4)
    except Exception as exc:
        record("stream", f"ERR {type(exc).__name__}: {exc}")

    print("\n[6] ASR（multipart，喂刚才生成的 mp3）")
    mp3 = Path(os.environ["TEMP"]) / "fish_speed_1.0.mp3"
    if mp3.exists():
        try:
            import httpx
            with open(mp3, "rb") as fh:
                t0 = time.perf_counter()
                rr = httpx.post(f"{BASE}/v1/asr",
                                headers={"Authorization": f"Bearer {KEY}", "model": "transcribe-1-pro"},
                                files={"audio": ("a.mp3", fh, "audio/mpeg")},
                                data={"ignore_timestamps": "false", "language": "zh"},
                                timeout=300)
                dt = (time.perf_counter() - t0) * 1000
                record("status", rr.status_code)
                record("wall_ms", round(dt))
                if rr.status_code == 200:
                    body = rr.json()
                    record("text", body.get("text"))
                    record("language", body.get("language"))
                    record("segments_n", len(body.get("segments") or []))
                    record("duration", body.get("duration"))
                else:
                    record("body", rr.text[:200])
        except Exception as exc:
            record("asr", f"ERR {type(exc).__name__}: {exc}")

    print("\n[7] 错误路径")
    rr, _ = tts("")  # 空文本
    record("empty_text", f"HTTP {rr.status_code} {rr.text[:120]}")
    import httpx
    rr = httpx.post(f"{BASE}/v1/tts", json={"text": "x"},
                    headers={"Authorization": "Bearer sk-fish-invalid", "Content-Type": "application/json",
                             "model": "s2.1-pro-free"}, timeout=60)
    record("bad_key", f"HTTP {rr.status_code} {rr.text[:120]}")

    print("\n[8] 钱包余额")
    try:
        import httpx
        rr = httpx.get(f"{BASE}/wallet/self/api-credit",
                       headers={"Authorization": f"Bearer {KEY}"}, timeout=60)
        record("credit", rr.json().get("credit"))
    except Exception as exc:
        record("credit", f"ERR {type(exc).__name__}")

    out = Path(os.environ["TEMP"]) / "fish_probe_results.json"
    out.write_text(json.dumps(RESULTS, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n结果落盘: {out}")
    return 0


def probe_duration(mp3: Path) -> float:
    import subprocess
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(mp3)],
        capture_output=True, text=True, encoding="utf-8", errors="replace")
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


if __name__ == "__main__":
    raise SystemExit(main())
