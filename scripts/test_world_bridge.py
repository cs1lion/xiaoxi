"""S5 pixel-world server bridge: begin/drive, tools, routing, background tasks.

    python scripts/test_world_bridge.py

No real model or browser: the world is a ledger projection, so the server
contract can be proven offline. The world-side real-frame e2e is a separate
QA gate.
"""
from __future__ import annotations

import asyncio
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.executive import (  # noqa: E402
    NO_WORKER_QUESTION, ExecutiveService,
)
from server.assistant.service import (  # noqa: E402
    WORLD_DRIVE_CONCURRENCY, AssistantService,
)
from server.dialogue.orchestrator import (  # noqa: E402
    delegation_intent, progress_query_intent, telemetry_query_intent,
)

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


ROSTER = {
    "selection": {"max_concurrent_default": 1},
    "dispatch": {"max_steps": 4, "max_prompt_tokens": 80000, "max_retry": 2,
                 "spin_guard": {"max_identical": 3}},
    "agents": [
        {"id": "scribe", "name": "笔录官", "adapter": "manual", "summary": "写文档",
         "strengths": ["写作"], "weaknesses": ["不碰代码"], "cost_tier": "cheap",
         "max_concurrent": 10},
    ],
}
NO_WORKER_ROSTER = {**ROSTER, "agents": []}

POS_QUERY = [
    "去像素世界里看看她在干什么", "像素世界现在什么情况", "世界里的铺子都开着吗",
    "去看看像素世界", "像素世界里有新的活吗", "把 pixel world 的状态报给我",
    "刷新一下像素世界的画面", "像素世界需不需要我帮忙", "帮我去像素世界那边瞅瞅",
    "像素世界 offline 了吗",
]
POS_DISPATCH = [
    "让像素世界接一单把脚本写完", "派一单给像素世界里的她：整理便笺",
    "像素世界接一单，把便笺整理成三版标题",
]
NEGATIVE = [
    "安排人把便笺做成三版", "让她去把这份稿子改了", "盯着这个单做完再叫我",
    "进度怎么样了", "战况烧了多少 token", "收工", "不行打回重做",
    "建个任务明天下午三点评审", "取消提醒", "便笺整理一下",
    "世界杯开始了吗", "你的世界观是什么", "昨晚世界杯看了没", "她最近世界观变了不少",
]


def new_exec(roster=ROSTER) -> ExecutiveService:
    return ExecutiveService(tempfile.mkdtemp(prefix="world-bridge-"), roster=roster)


def route_with(text: str, world: bool) -> bool:
    """Real should_route regexes, with the world branch toggled for a diff."""
    stub = AssistantService.__new__(AssistantService)
    stub.note_conversation = SimpleNamespace(should_route=lambda *a, **k: False)
    stub.thread_conversation = SimpleNamespace(should_route=lambda *a, **k: False)
    stub._activity_enabled = lambda: True
    stub._world_request = (AssistantService._world_request if world else (lambda _: False))
    return AssistantService.should_route(stub, text)


def test_begin_and_delegate() -> None:
    print("[begin_commission / delegate]")
    exec_ = new_exec()
    try:
        begun = exec_.begin_commission("整理选题", "把七月选题理成三版标题")
        check("begin 成功进入 running", begun["status"] == "running", str(begun))
        check("begin 返回 agent_name", begun["agent_name"] == "笔录官", str(begun))
        check("begin 台账确实 running",
              exec_.commission(begun["commission_id"])["status"] == "running")
        check("begin 返回四个字段",
              set(begun) == {"commission_id", "status", "agent_id", "agent_name"},
              str(sorted(begun)))
        check("agent_name 公开查询", exec_.agent_name("scribe") == "笔录官")
        check("未知 Agent 名字为空", exec_.agent_name("nobody") == "")

        async def fake_drive(commission_id):
            return exec_.commission(commission_id)

        exec_.drive = fake_drive
        result = asyncio.run(exec_.delegate("把稿子整理一下"))
        check("delegate 返回形状不变",
              set(result) == {"commission_id", "status", "agent_id", "agent_name"},
              str(sorted(result)))
        check("delegate 正常路径保持 running", result["status"] == "running", str(result))
    finally:
        exec_.close()

    no_worker = new_exec(NO_WORKER_ROSTER)
    try:
        blocked = no_worker.begin_commission("没人接的单", "试一下")
        check("无将时 begin 返回 blocked", blocked["status"] == "blocked", str(blocked))
        check("无将时带标准请示",
              no_worker.commission(blocked["commission_id"])["blocked_question"]
              == NO_WORKER_QUESTION,
              str(no_worker.commission(blocked["commission_id"]).get("blocked_question")))
        delegated = asyncio.run(no_worker.delegate("还是没人接"))
        check("无将时 delegate 不抛且形状不变",
              delegated["status"] == "blocked" and
              set(delegated) == {"commission_id", "status", "agent_id", "agent_name"},
              str(delegated))
    finally:
        no_worker.close()

    broken = new_exec()
    try:
        def explode(*args, **kwargs):
            raise RuntimeError("boom")
        broken.dispatcher.dispatch = explode
        failed_begin = broken.begin_commission("派单炸了", "试试异常路径")
        check("dispatch 异常时落 blocked", failed_begin["status"] == "blocked",
              str(failed_begin))
        question = broken.commission(failed_begin["commission_id"])["blocked_question"]
        check("异常请示带类型", "RuntimeError" in question, question)
    finally:
        broken.close()

    drive_broken = new_exec()
    try:
        async def explode_drive(commission_id):
            raise RuntimeError("drive-boom")
        drive_broken.drive = explode_drive
        delegated = asyncio.run(drive_broken.delegate("驱动也炸了"))
        check("delegate 驱动异常不外抛", delegated["status"] == "blocked", str(delegated))
    finally:
        drive_broken.close()

    shadowed = new_exec()
    shadow_calls: list[str] = []

    async def shadow(commission):
        shadow_calls.append(commission["id"])

    shadowed.shadow = shadow

    async def shadow_scenario():
        started = shadowed.begin_commission("shadow 只启一次", "测试")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return started

    try:
        asyncio.run(shadow_scenario())
        check("begin 启动 shadow 恰一次", len(shadow_calls) == 1, str(shadow_calls))
    finally:
        shadowed.close()


def test_world_tools() -> None:
    print("[world.dispatch / world.status]")
    service = AssistantService(Path(tempfile.mkdtemp(prefix="world-svc-")),
                              SimpleNamespace(offline=True))
    driven: list[str] = []
    begun: list[tuple[str, str]] = []
    counter = 0

    def fake_begin(title, brief="", thread_id="", *, intent="", acceptance=None,
                   request_id=None):
        nonlocal counter
        counter += 1
        commission_id = f"c_world{counter}"
        begun.append((title, brief))
        return {"commission_id": commission_id, "status": "running",
                "agent_id": "scribe", "agent_name": "笔录官"}

    async def fake_drive(commission_id):
        driven.append(commission_id)

    service.executive.begin_commission = fake_begin
    service.executive.drive = fake_drive
    try:
        tools = {tool.name: tool for tool in service.tools}
        check("两个世界工具已注册",
              {"world.dispatch", "world.status"} <= set(tools), str(sorted(tools)))
        check("world.dispatch 是 mutation", tools["world.dispatch"].mutation is True)
        check("world.dispatch 接服务端幂等键",
              tools["world.dispatch"].idempotency_key == "request_id")
        check("world.status 只读", tools["world.status"].mutation is False)

        # The ledger itself must dedupe before a worker is ever dispatched.
        from server.interaction.threads import CommissionStore
        ledger = CommissionStore(":memory:")
        try:
            first = ledger.create("幂等世界单", "同一件事", request_id="op-world-1")
            replay = ledger.create("幂等世界单", "同一件事", request_id="op-world-1")
            check("台账 request_id 返回同一单", first["id"] == replay["id"],
                  f"{first['id']} / {replay['id']}")
            check("台账可按 request_id 反查",
                  ledger.request_commission("op-world-1")["id"] == first["id"])
            try:
                ledger.create("另一件事", "", request_id="op-world-1")
            except ValueError as exc:
                check("同一 request_id 换参数被拒", "different arguments" in str(exc), str(exc))
            else:
                check("同一 request_id 换参数被拒", False)
        finally:
            ledger.close()

        async def dispatch_scenario():
            only_title = service._world_dispatch("只有标题")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            with_brief = service._world_dispatch(
                "整理选题", "把七月选题理成三版标题")
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            return only_title, with_brief

        only_title, with_brief = asyncio.run(dispatch_scenario())
        check("仅传 title 不 TypeError", only_title["id"] == "c_world1",
              str(only_title))
        check("receipt id == commission_id",
              with_brief["id"] == with_brief["commission_id"] == "c_world2",
              str(with_brief))
        check("receipt world_visible 恒为 unknown",
              with_brief["world_visible"] == "unknown", str(with_brief))
        check("brief 原样进 begin",
              begun == [("只有标题", ""), ("整理选题", "把七月选题理成三版标题")],
              str(begun))
        check("每次派单各驱动一次",
              driven == ["c_world1", "c_world2"], str(driven))
        check("任务从强引用集合移除", not service._world_tasks, str(service._world_tasks))

        check("world.dispatch 校验 title 必填",
              tools["world.dispatch"].validate({"title": "x"}) == {"title": "x"})
        for bad in ({}, {"title": "x", "extra": 1}, {"title": 3}):
            try:
                tools["world.dispatch"].validate(bad)
                ok = False
            except ValueError:
                ok = True
            check(f"非法参数被拒 {bad}", ok)

        async def busy_scenario():
            blockers = [asyncio.create_task(asyncio.sleep(60))
                        for _ in range(WORLD_DRIVE_CONCURRENCY)]
            service._world_tasks.update(blockers)
            try:
                return service._world_dispatch("第三单")
            finally:
                for task in blockers:
                    task.cancel()
                await asyncio.gather(*blockers, return_exceptions=True)

        busy = asyncio.run(busy_scenario())
        check("并发满时返回 busy 且不建单",
              busy["status"] == "busy" and busy["commission_id"] == "" and
              busy.get("retryable") is True, str(busy))
        check("busy 没有多开单", len(begun) == 2, str(begun))

        # status: explicit projection, no packet / memory text.
        secret = "SECRET-MEMORY"
        service.executive.commission = lambda commission_id: {
            "id": commission_id, "status": "blocked", "agent_id": "scribe",
            "blocked_question": "要放行吗？", "delivery": {"summary": "已整理三版"},
            "updated_at": 123.0, "packet": {"memory_block": secret},
        }
        service.executive.agent_name = lambda agent_id: "笔录官"
        view = service._world_status("c_world1")
        check("status 投影字段齐全",
              view["found"] is True and view["status"] == "blocked" and
              view["blocked_question"] == "要放行吗？" and
              view["delivery_summary"] == "已整理三版", str(view))
        check("status 不泄漏 packet",
              "packet" not in view and secret not in str(view), str(view))
        check("status 标记不可信数据",
              view["context_is_untrusted_data"] is True and
              view["grants_authorization"] is False)

        service.executive.commission = lambda commission_id: (_ for _ in ()).throw(
            KeyError(commission_id))
        check("查无此单显式 found=false",
              service._world_status("missing") == {"found": False})

        # Background task exception: log + legal block, never escape.
        fake = SimpleNamespace(
            drive=None, blocked=[],
            commission=lambda cid: {"status": "running"},
            dispatcher=SimpleNamespace(
                block=lambda cid, question: fake.blocked.append((cid, question))),
        )

        async def broken_drive(commission_id):
            raise RuntimeError("background-boom")

        fake.drive = broken_drive
        probe = AssistantService.__new__(AssistantService)
        probe.executive = fake
        probe._world_tasks = set()
        asyncio.run(probe._drive_world_commission("c_bg"))
        check("后台驱动异常落 blocked",
              fake.blocked == [("c_bg", "驱动中断，要我再试一次吗？")],
              str(fake.blocked))

        closer = AssistantService(Path(tempfile.mkdtemp(prefix="world-close-")),
                                  SimpleNamespace(offline=True))

        async def close_scenario():
            task = asyncio.create_task(asyncio.sleep(60))
            closer._world_tasks.add(task)
            closer.close()
            await asyncio.sleep(0)
            return task

        task = asyncio.run(close_scenario())
        check("close 取消未完成的世界任务",
              task.cancelled() or task.cancelling(), str(task))
    finally:
        service.close()


def test_routing() -> None:
    print("[routing diff]")
    for text in POS_QUERY + POS_DISPATCH:
        before, after = route_with(text, False), route_with(text, True)
        check(f"世界正例改判 {text}", not before and after,
              f"before={before} after={after}")
    for text in NEGATIVE:
        before, after = route_with(text, False), route_with(text, True)
        check(f"反例差分一致 {text}", before == after,
              f"before={before} after={after}")

    check("派单正例不被快环截走",
          all(not delegation_intent(text) for text in POS_DISPATCH),
          str([text for text in POS_DISPATCH if delegation_intent(text)]))
    for text in ("安排人把便笺做成三版", "让她去把这份稿子改了",
                 "盯着这个单做完再叫我"):
        check(f"既有快环派单不变 {text}", delegation_intent(text))
    check("进度快环不变", progress_query_intent("进度怎么样了"))
    check("战况快环不变", telemetry_query_intent("战况烧了多少 token"))
    check("大小写 pixel world",
          route_with("Pixel World 现在什么情况", True) and
          route_with("PIXEL WORLD 现在什么情况", True))
    check("裸世界不误伤", not route_with("世界杯开始了吗", True))


def main() -> int:
    test_begin_and_delegate()
    test_world_tools()
    test_routing()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for item in failed:
        print(f"  - {item}")
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
