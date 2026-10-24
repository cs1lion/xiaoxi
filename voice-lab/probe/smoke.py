#!/usr/bin/env python3
"""
voice-lab 探针 —— 端点/模型握手冒烟（零音频消耗，先探通再坐实）

背景（来自 research §1）：老 key 的 LLM_BASE_URL 是 step_plan/v1；
step_plan 的 realtime 端点存在（.../step_plan/v1/realtime，文档称「参数与开放平台一致」），
但其语音模型表只列 stepaudio-2.5-realtime，未列 stepaudio-3。
故「老key+stepaudio-3-realtime-preview」可能在连上/首响应即被拒。
本脚本只做：连 WS → 收 session.created → 发 session.update → 收 session.updated/error，
**不发音频、不触发模型出声**，把「端点 × 模型」组合逐个试一遍，报告哪个握手成功。

用法：
  python voice-lab/probe/smoke.py            # 用 voice-lab/.env 里的 key，试默认端点矩阵
凭据只读 voice-lab/.env（主 LLM_API_KEY 是否写入该 .env 由人决定；本脚本绝不读根 .env）。
"""

import asyncio
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LAB = HERE.parent
ENV_FILE = LAB / ".env"

MODELS = ["stepaudio-3-realtime-preview", "stepaudio-2.5-realtime"]
# 两个端点基址：开放平台 + step_plan（老 key 很可能属后者）
ENDPOINT_BASES = [
    "wss://api.stepfun.com/v1/realtime",
    "wss://api.stepfun.com/step_plan/v1/realtime",
]


def load_env():
    if not ENV_FILE.exists():
        return None
    kv = {}
    for line in ENV_FILE.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            kv[k.strip()] = v.strip().strip('"').strip("'")
    key = kv.get("STEP_REALTIME_API_KEY", "")
    return key or None


async def handshake(ws_mod, base, model, key):
    url = f"{base}?model={model}"
    try:
        async with ws_mod.connect(url, additional_headers={"Authorization": f"Bearer {key}"},
                                  max_size=8 * 1024 * 1024) as ws:
            created = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
            await ws.send(json.dumps({"type": "session.update",
                                      "session": {"modalities": ["text", "audio"],
                                                  "turn_detection": None}}))
            # 读几条，直到 session.updated 或 error 或超时
            seen = [created.get("type")]
            updated_or_err = None
            for _ in range(4):
                try:
                    evt = json.loads(await asyncio.wait_for(ws.recv(), timeout=6))
                except asyncio.TimeoutError:
                    break
                seen.append(evt.get("type"))
                if evt.get("type") == "session.updated":
                    updated_or_err = ("updated", evt.get("session", {}).get("model"))
                    break
                if evt.get("type") == "error":
                    updated_or_err = ("error", evt.get("error", {}))
                    break
            return {"ok": updated_or_err and updated_or_err[0] == "updated",
                    "detail": updated_or_err, "events": seen}
    except Exception as e:
        return {"ok": False, "detail": f"{type(e).__name__}: {str(e)[:180]}"}


def main():
    key = load_env()
    if not key:
        print("smoke BLOCKED: voice-lab/.env 无 STEP_REALTIME_API_KEY")
        sys.exit(4)
    import websockets
    print(f"[smoke] key 指纹 len={len(key)}；不发音频，仅握手矩阵")
    results = []

    async def go():
        for base in ENDPOINT_BASES:
            for model in MODELS:
                r = await handshake(websockets, base, model, key)
                tag = f"{base.split('api.stepfun.com')[1]} :: {model}"
                results.append((tag, r))
                status = "握手OK" if r["ok"] else ("被拒" if r["detail"] and r["detail"][0] == "error" else "连不上")
                print(f"  [{status}] {tag}")
                if not r["ok"]:
                    print(f"        → {r['detail']}")

    asyncio.run(go())
    viable = [t for t, r in results if r["ok"]]
    print("\n[smoke 结论] 可握手成功的端点×模型：")
    print("  " + ("、".join(viable) if viable else "（全部失败 → 老 key 不能直连 realtime，坐实停跑）"))
    (LAB / "outputs").mkdir(exist_ok=True)
    (LAB / "outputs" / "smoke-handshake.json").write_text(
        json.dumps({t: r for t, r in results}, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
