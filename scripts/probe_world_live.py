"""Live S5 probe: does the real fast-loop model select the world tools?

Temporary assistant/ledger directory; no real `data/` is touched. The real
projector is intentionally not started: the probe checks tool selection,
mutation confirmation, and the receipt shape; the world-side render is QA's
real-frame e2e.

    python scripts/probe_world_live.py

Costs a few model calls. Exits 0 only when all three scenarios pass.
"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.service import AssistantService  # noqa: E402
from server.llm import llm  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def observations(service: AssistantService, run_id: str) -> list[dict]:
    try:
        state = service.runtime.get(run_id)["state"]
    except Exception:
        return []
    return [item for item in (state.get("observations") or []) if isinstance(item, dict)]


def world_tools(items: list[dict]) -> list[dict]:
    return [item for item in items if str(item.get("tool", "")).startswith("world.")]


async def dispatch_scenario(service: AssistantService) -> dict:
    print("[live world.dispatch]")
    text = "让像素世界接一单把脚本写完"
    result = await service.start(text, "live-s5-dispatch")
    before = len(service.executive.commissions())
    confirmation = result.get("confirmation") or {}
    check("模型进入确认门而不是直接执行",
          result.get("status") == "waiting_confirmation", str(result)[:500])
    check("确认卡绑定 world.dispatch",
          confirmation.get("tool") == "world.dispatch", str(confirmation)[:400])
    check("确认前台账没有新单", before == 0, f"before={before}")

    if result.get("status") != "waiting_confirmation" or not confirmation.get("id"):
        return {"result": result}
    resumed = await service.resume(result["run_id"], confirmation["id"], True)
    await asyncio.sleep(0.2)
    items = observations(service, result["run_id"])
    receipt = next((item for item in items if item.get("tool") == "world.dispatch"), None)
    data = (receipt or {}).get("data") or {}
    after = len(service.executive.commissions())
    check("批准后 world.dispatch 回执成功",
          bool(receipt) and receipt.get("status") == "succeeded", str(receipt)[:500])
    check("回执 id == commission_id",
          bool(data) and data.get("id") == data.get("commission_id"), str(data)[:300])
    check("回执不声称世界已开工",
          data.get("world_visible") == "unknown", str(data)[:300])
    check("批准后真的落了一单", after == before + 1, f"before={before} after={after}")
    print("  receipt:", json.dumps(data, ensure_ascii=False)[:500])
    print("  resumed:", json.dumps(resumed, ensure_ascii=False)[:500])
    return {"result": result, "resumed": resumed, "receipt": data}


async def status_scenario(service: AssistantService) -> dict:
    print("[live world.status]")
    commission = service.executive.open("世界现状", "整理一份简报")
    cid = commission["id"]
    text = f"像素世界里的 {cid} 现在什么情况？"
    result = await service.start(text, "live-s5-status")
    items = observations(service, result["run_id"])
    receipt = next((item for item in items if item.get("tool") == "world.status"), None)
    data = (receipt or {}).get("data") or {}
    check("模型选了 world.status", receipt is not None, str(result)[:600])
    check("状态回执找到这单",
          bool(data) and data.get("found") is True and data.get("id") == cid,
          str(data)[:400])
    check("状态回执不泄漏 packet",
          "packet" not in json.dumps(data, ensure_ascii=False), str(data)[:300])
    print("  result:", json.dumps(result, ensure_ascii=False)[:500])
    print("  receipt:", json.dumps(data, ensure_ascii=False)[:500])
    return {"result": result, "receipt": data}


async def ordinary_scenario(service: AssistantService) -> dict:
    print("[live non-world request]")
    text = "提醒我明天下午三点开会"
    result = await service.start(text, "live-s5-ordinary")
    confirmation = result.get("confirmation") or {}
    items = observations(service, result["run_id"])
    chosen = [confirmation.get("tool")] if confirmation.get("tool") else []
    chosen += [item.get("tool") for item in items]
    world = [name for name in chosen if str(name).startswith("world.")]
    check("普通请求不误选世界工具", not world, str(world))
    print("  result:", json.dumps(result, ensure_ascii=False)[:500])
    return {"result": result, "chosen": chosen}


async def main_async() -> int:
    if getattr(llm, "offline", False):
        print("SKIP: LLM 离线（没有 API Key）")
        return 0
    directory = Path(tempfile.mkdtemp(prefix="xiaoxi-world-live-"))
    service = AssistantService(directory / "assistant", llm)
    # Skip the extra planner model call; this probe is about tool selection.
    service.loop.planner = None
    driven: list[str] = []

    async def fake_drive(commission_id: str) -> None:
        driven.append(commission_id)

    service.executive.drive = fake_drive
    try:
        report = {
            "dispatch": await dispatch_scenario(service),
            "status": await status_scenario(service),
            "ordinary": await ordinary_scenario(service),
        }
        await asyncio.sleep(0.2)
        check("后台驱动被调用一次且只一次", len(driven) == 1, str(driven))
        print("\n[report]")
        print(json.dumps(report, ensure_ascii=False, indent=2)[:4000])
    finally:
        service.close()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main_async()))
