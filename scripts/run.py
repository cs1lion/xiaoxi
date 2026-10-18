"""一键启动：主应用 + 本地 Anthropic 网关。

    python scripts/run.py            # 起网关（若未起）和主服务
    python scripts/run.py --check    # 顺便跑一遍端到端联调
    python scripts/run.py --no-gateway

网关是 Claude Code 当 Worker 的必需件：主应用本身不依赖它，但真机 Claude
派单需要 127.0.0.1:15722。已经手动起过网关时不会重复起，也不会在退出时杀掉它。
"""

from __future__ import annotations

import argparse
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

GATEWAY_HOST = "127.0.0.1"
GATEWAY_PORT = 15722


def gateway_health(host: str = GATEWAY_HOST, port: int = GATEWAY_PORT) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def app_health(host: str, port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://{host}:{port}/api/health", timeout=1.5) as response:
            return response.status == 200
    except Exception:
        return False


def port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def start_gateway() -> subprocess.Popen | None:
    if gateway_health():
        print(f"  网关已在运行：http://{GATEWAY_HOST}:{GATEWAY_PORT}")
        return None
    if port_open(GATEWAY_HOST, GATEWAY_PORT):
        print(f"  警告：{GATEWAY_PORT} 已被占用，但不是本项目的网关；Claude Worker 不可用。")
        return None
    cmd = [
        # The gateway needs the project's FastAPI/httpx pins; the app itself may
        # be launched by a different interpreter (the current machine runs it on
        # anaconda). Prefer the project venv when it exists.
        str(ROOT / ".venv" / "Scripts" / "python.exe")
        if (ROOT / ".venv" / "Scripts" / "python.exe").is_file() else sys.executable,
        "-m", "uvicorn",
        "server.gateway.anthropic_proxy:app",
        "--host", GATEWAY_HOST,
        "--port", str(GATEWAY_PORT),
        "--log-level", "warning",
    ]
    process = subprocess.Popen(cmd, cwd=str(ROOT))
    for _ in range(50):
        if gateway_health():
            print(f"  网关已启动：http://{GATEWAY_HOST}:{GATEWAY_PORT}")
            return process
        if process.poll() is not None:
            break
        time.sleep(0.2)
    print("  警告：网关没有起来；Claude Worker 会以“网关不可用”阻塞，主应用继续。")
    return process


def stop_gateway(process: subprocess.Popen | None) -> None:
    if process is None or process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=5)
    except Exception:
        try:
            process.kill()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--check", action="store_true", help="启动前跑一次离线自检")
    parser.add_argument("--no-gateway", action="store_true",
                        help="不自动启动 Anthropic 网关（只用手动 Agent 时可用）")
    args = parser.parse_args()

    if args.check:
        from server.config import settings

        print(f"LLM: {settings.llm_provider}/{settings.llm_model} key={'有' if settings.llm_api_key else '无(离线)'}")
        print(f"TTS: {settings.tts_engine}   ASR: {settings.asr_model}")

    gateway = None
    try:
        if not args.no_gateway:
            print("\n[gateway]")
            gateway = start_gateway()
        if app_health(args.host, args.port):
            print(f"  主应用已在运行：http://{args.host}:{args.port}（不重复启动）")
            return 0
        if port_open(args.host, args.port):
            print(f"  端口 {args.port} 已被占用，但不是本项目的主应用；"
                  "请换 --port 或先停掉占用者。")
            return 1
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
    finally:
        stop_gateway(gateway)


if __name__ == "__main__":
    raise SystemExit(main())
