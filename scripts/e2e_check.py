"""端到端联调：WebSocket 文本 + 语音（合成 mp3 -> ffmpeg -> 16k PCM -> WS -> 音频回来）

用法：先启动服务，再
    python scripts/e2e_check.py
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx
import websockets

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = "http://127.0.0.1:8000"
WS_URL = "ws://127.0.0.1:8000/ws"
PHRASE = "我昨天说的那个面试，今天下午就要开始了。"


async def collect(ws, timeout: float = 90.0) -> list[dict]:
    events: list[dict] = []
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            events.append(json.loads(raw))
            if events[-1]["type"] in ("done", "error", "interrupted"):
                return events
    except asyncio.TimeoutError:
        return events


async def main() -> int:
    global BASE, WS_URL
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--skip-memory-recall", action="store_true",
                        help="离线 mock 模式下没有语义，跳过'接上记忆'这一项")
    args = parser.parse_args()
    BASE = f"http://{args.host}:{args.port}"
    WS_URL = f"ws://{args.host}:{args.port}/ws"

    problems: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, timeout=20) as client:
        health = (await client.get("/api/health")).json()
        print("health:", health)

        await client.post("/api/memory/reset", json={})
        seeded = await client.post(
            "/api/memory/manual",
            json={"kind": "episode", "subject": "user", "key": "计划:周五面试",
                  "value": "用户说周五有一场面试", "importance": 0.85},
        )
        memory_id = seeded.json()["id"]
        listed = (await client.get("/api/memories")).json()
        print(f"memories after seed: {listed['count']}")
        if listed["count"] != 1:
            problems.append("记忆写入失败")

        async with websockets.connect(WS_URL, max_size=16 * 1024 * 1024) as ws:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            print("ready:", hello["persona"], hello["session_id"])

            await ws.send(json.dumps({"type": "text", "text": "我面试完了。"}))
            events = await collect(ws)
            reply = next((e for e in events if e["type"] == "text"), None)
            audio = [e for e in events if e["type"] == "audio"]
            print(f"text reply: {reply['text'] if reply else None!r} emotion={reply and reply['emotion']} audio_chunks={len(audio)}")
            if not reply or not reply["text"]:
                problems.append("文本回复为空")
            if not audio:
                problems.append("没有返回音频")
            else:
                size = sum(len(base64.b64decode(e["data"])) for e in audio)
                print(f"audio bytes: {size}")
                if size < 2000:
                    problems.append("音频过短")

        # 语音链路：edge-tts -> ffmpeg -> pcm16@16k -> WS
        from server.speech.tts import tts

        mp3 = await tts.synthesize(PHRASE, "base")
        with tempfile.TemporaryDirectory() as tmp:
            mp3_path = Path(tmp) / "a.mp3"
            wav_path = Path(tmp) / "a.wav"
            mp3_path.write_bytes(mp3)
            subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(mp3_path),
                 "-ar", "16000", "-ac", "1", str(wav_path)],
                check=True,
            )
            import numpy as np

            from server.speech.asr import decode_wav

            audio_f32, rate = decode_wav(wav_path.read_bytes())
            pcm = (np.clip(audio_f32, -1, 1) * 32767).astype("<i2").tobytes()
            async with websockets.connect(WS_URL, max_size=16 * 1024 * 1024) as ws:
                json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
                await ws.send(json.dumps({"type": "start"}))
                for i in range(0, len(pcm), 3200):
                    await ws.send(pcm[i : i + 3200])
                    await asyncio.sleep(0.01)
                await ws.send(json.dumps({"type": "audio_end"}))
                events = await collect(ws)
                asr = next((e for e in events if e["type"] == "asr"), None)
                reply = next((e for e in events if e["type"] == "text"), None)
                print(f"asr: {asr and asr['text']!r} ({asr and asr['duration_ms']}ms)")
                print(f"voice reply: {reply and reply['text']!r}")
                if not asr or not asr["text"]:
                    problems.append("语音识别为空")
                elif "面试" not in asr["text"]:
                    problems.append(f"识别内容不对: {asr['text']}")
                if not args.skip_memory_recall and "面试" not in (reply and reply["text"] or ""):
                    problems.append("没有接上之前的记忆")

        # 删除是硬删除：查不到、也搜不到
        deleted = await client.delete(f"/api/memories/{memory_id}")
        print("delete:", deleted.json())
        after = (await client.get("/api/memories")).json()
        if after["count"] != 0:
            problems.append("删除后仍有残留")

    print("\n=== " + ("ALL OK" if not problems else "FAIL: " + "; ".join(problems)) + " ===")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))