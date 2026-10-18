"""Run the Anthropic Messages gateway on loopback.

    .\\.venv\\Scripts\\python.exe scripts\\run_gateway.py [--port 15722]

Kept out of the main app on purpose: it is a worker-side component, not a
browser-facing route, and it must be able to restart with the worker fleet.
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import uvicorn  # noqa: E402

from server.gateway.anthropic_proxy import app  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=15722)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
