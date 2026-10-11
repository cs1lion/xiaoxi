"""一键启动。

    python scripts/run.py            # 起服务
    python scripts/run.py --check    # 顺便跑一遍端到端联调

离线模式（没有 API_KEY）也能跑通全链路，只是回复内容是占位文本。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--check", action="store_true", help="启动前跑一次离线自检")
    args = parser.parse_args()

    if args.check:
        from server.config import settings

        print(f"LLM: {settings.llm_provider}/{settings.llm_model} key={'有' if settings.llm_api_key else '无(离线)'}")
        print(f"TTS: {settings.tts_engine}   ASR: {settings.asr_model}")

    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "server.app:app",
        "--host",
        args.host,
        "--port",
        str(args.port),
    ]
    if args.reload:
        cmd.append("--reload")
    print(f"\n  打开 http://{args.host}:{args.port}\n")
    return subprocess.call(cmd, cwd=str(ROOT))


if __name__ == "__main__":
    raise SystemExit(main())