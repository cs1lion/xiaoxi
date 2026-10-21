"""网关启动环境的接缝：上游默认落在 .env，不默认依赖另一个产品的端口。

    python -B scripts/test_gateway_env.py

为什么值得一个测试：15722 网关默认上游是 `127.0.0.1:15721`（CC Switch），那是
另一个产品在本机另起的进程。它没起来时网关 `/health` 照样 200，于是"网关起了"
看起来没问题，但每一次 Worker 调用都 502 `upstream primary unavailable`——
2026-10-18 的 DSH 接管实测就是这样失败的，失败信息只在 Worker 的错误行里。

`scripts/run.py` 因此把默认上游改成 .env 的 LLM_BASE_URL。这条测试钉住三件事：
默认值确实改了、显式设置的 AQI_GATEWAY_UPSTREAM 不被覆盖、缺 key 时不塞空串。
零网络、零子进程：只读 run.py 的 gateway_env()。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

import importlib.util  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def load_run_module():
    """run.py 是脚本，没有 `if __name__` 之外的 import 副作用，可以直接 import。"""
    spec = importlib.util.spec_from_file_location("xiaoxi_run", ROOT / "scripts" / "run.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    from server.config import settings

    run = load_run_module()

    print("[gateway env: default upstream is the project's own]")
    for name in ("AQI_GATEWAY_UPSTREAM", "AQI_GATEWAY_KEY", "AQI_GATEWAY_MODEL"):
        os.environ.pop(name, None)
    env = run.gateway_env()
    check("默认上游不是 15721（外部产品的端口）",
          env.get("AQI_GATEWAY_UPSTREAM") != "http://127.0.0.1:15721/v1",
          str(env.get("AQI_GATEWAY_UPSTREAM")))
    check("默认上游落在 .env 的 LLM_BASE_URL",
          env.get("AQI_GATEWAY_UPSTREAM") == settings.llm_base_url,
          str(env.get("AQI_GATEWAY_UPSTREAM")))
    check("模型名跟着 .env 走", env.get("AQI_GATEWAY_MODEL") == settings.llm_model,
          str(env.get("AQI_GATEWAY_MODEL")))

    print("[gateway env: explicit values win]")
    os.environ["AQI_GATEWAY_UPSTREAM"] = "http://127.0.0.1:15721/v1"
    os.environ["AQI_GATEWAY_MODEL"] = "step-5-preview"
    env = run.gateway_env()
    check("显式设了 AQI_GATEWAY_UPSTREAM 就不覆盖",
          env.get("AQI_GATEWAY_UPSTREAM") == "http://127.0.0.1:15721/v1",
          str(env.get("AQI_GATEWAY_UPSTREAM")))
    check("显式设了模型名也不覆盖",
          env.get("AQI_GATEWAY_MODEL") == "step-5-preview", str(env.get("AQI_GATEWAY_MODEL")))
    os.environ.pop("AQI_GATEWAY_UPSTREAM", None)
    os.environ.pop("AQI_GATEWAY_MODEL", None)

    print("[gateway env: no key does not inject an empty one]")
    from server.config import settings as fresh

    saved = fresh.llm_api_key
    try:
        fresh.llm_api_key = ""
        os.environ.pop("AQI_GATEWAY_KEY", None)
        env = run.gateway_env()
        check("没有 key 时不塞空串（空 key 会让上游 401 而不是本地报错）",
              "AQI_GATEWAY_KEY" not in env, str(env.get("AQI_GATEWAY_KEY")))
    finally:
        fresh.llm_api_key = saved

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for name in failed:
            print("  -", name)
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
