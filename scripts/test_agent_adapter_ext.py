"""适配器与注册表扩展性自检：验证"零侵入新增一种 Agent 类型"真的成立。

    python scripts/test_agent_adapter_ext.py

核心断言只有一条：新增一种干活方式，只需要写一个 BaseWorkerAdapter 子类并
@register_adapter，调度核心（dispatcher.py）一行代码都不用改。测试用一个
Mock 适配器把这条断言走成事实，而不是靠读代码相信它。

注意 Mock 用类级状态：Dispatcher 每次 run_commission 都会新建一个适配器实例，
所以"配置要发什么事件"必须挂在类上，挂在实例上那个实例根本不会被用到。
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.adapters import (BaseWorkerAdapter, adapter_types,  # noqa: E402
                                       create_adapter, create_adapter_for,
                                       register_adapter)
from server.assistant.adapters.base import (AdapterError, UnknownAdapterError,  # noqa: E402
                                            artifact_event, make_event, token_event)
from server.assistant.dispatcher import select_agent  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name} {detail}")


def raises(name: str, fn, exception) -> None:
    try:
        fn()
    except exception:
        check(name, True)
    except Exception as exc:  # noqa: BLE001
        check(name, False, f"raised {type(exc).__name__}: {exc}")
    else:
        check(name, False, "did not raise")


GOOD_ARTIFACT = {
    "summary": "Mock 适配器按派单密件整理完，产出已列出。",
    "intercepted": "它想直接在顶栏加按钮，我按铁律拦下了。",
    "artifacts": [{"kind": "note", "ref": "note:n_mock@1", "preview": "mock 产出"}],
    "wired": True,
    "touched_ui": ["drawer"],
}


class RecordingAdapter(BaseWorkerAdapter):
    """只实现基类要求的三个方法，其余一律不碰；同时充当事件流记录仪。"""

    adapter_type = "mock_recording"
    instances: list["RecordingAdapter"] = []
    # 类级可调：Dispatcher 每次都会新建实例，挂在实例上的配置那个实例用不到。
    raise_on_execute: Exception | None = None
    default_events: list[dict] = [
        make_event("progress", step_idx=1, text="读完派单密件，开始整理"),
        token_event(model="mock-model", prompt_tokens=4321, completion_tokens=120,
                    ttft_ms=180, duration_ms=900, status_code=200),
        artifact_event(GOOD_ARTIFACT),
    ]

    def __init__(self, config: dict | None = None):
        super().__init__(config)
        self.prepared: list[tuple[str, dict]] = []
        self.cancelled = 0
        RecordingAdapter.instances.append(self)

    async def prepare_context(self, commission_id: str, briefing_packet: dict) -> dict:
        self.prepared.append((commission_id, dict(briefing_packet or {})))
        return {"commission_id": commission_id, "env": {"MOCK_ADAPTER": "1"}}

    async def execute(self, task_spec: dict):
        if self.raise_on_execute is not None:
            raise self.raise_on_execute
        context = task_spec.get("context") or {}
        if context.get("env", {}).get("MOCK_ADAPTER") != "1":
            # prepare_context 的产出必须原样流进 execute，否则适配器契约是断的
            raise AdapterError("prepare_context 的产出没有传进 execute")
        for event in type(self).default_events:
            yield event

    async def cancel(self) -> None:
        self.cancelled += 1


class SpinningAdapter(RecordingAdapter):
    adapter_type = "mock_spinning"


class SlowAdapter(RecordingAdapter):
    """执行中会真的 await：用来验证并发下只有一个执行体起身。"""

    adapter_type = "mock_slow"

    async def execute(self, task_spec):
        context = task_spec.get("context") or {}
        if context.get("env", {}).get("MOCK_ADAPTER") != "1":
            raise AdapterError("prepare_context 的产出没有传进 execute")
        await asyncio.sleep(0.05)
        for event in type(self).default_events:
            yield event


class ArtifactThenErrorAdapter(RecordingAdapter):
    """先交差再报错：这一版不算数。"""

    adapter_type = "mock_artifact_then_error"
    default_events = [make_event("progress", step_idx=1, text="干了一半的活"),
                      artifact_event(GOOD_ARTIFACT),
                      make_event("error", step_idx=2, text="干到一半崩了")]


class BrokenStartAdapter(RecordingAdapter):
    adapter_type = "mock_broken_start"

    async def prepare_context(self, commission_id: str, briefing_packet: dict) -> dict:
        raise AdapterError("executable 不存在")


class BrandNewAdapter(RecordingAdapter):
    """模拟"未来新增的一种 Agent 类型"：全新类型名、全新类，调度核心零改动。"""

    adapter_type = "brand_new_type"


register_adapter("mock_recording", RecordingAdapter)
register_adapter("mock_spinning", SpinningAdapter)
register_adapter("mock_slow", SlowAdapter)
register_adapter("mock_artifact_then_error", ArtifactThenErrorAdapter)
register_adapter("mock_broken_start", BrokenStartAdapter)
register_adapter("brand_new_type", BrandNewAdapter)


def build_service(extra_agents: list[dict]) -> ExecutiveService:
    base = copy.deepcopy(load_roster())
    base["agents"] = [agent for agent in base.get("agents", []) if agent.get("id") not in
                      {item.get("id") for item in extra_agents}]
    base["agents"].extend(copy.deepcopy(extra_agents))
    directory = Path(tempfile.mkdtemp(prefix="adapter-ext-"))
    return ExecutiveService(directory, roster=base)


MOCK_AGENT = {
    "id": "mock", "name": "mock 工", "adapter": "mock_recording",
    "strengths": ["整理", "mock"], "weaknesses": [], "cost_tier": "cheap",
    "max_concurrent": 2, "eval_commands": ["required_fields", "artifacts_exist", "token_budget"],
}


def last_instance(cls=RecordingAdapter) -> RecordingAdapter:
    """最近一次被调度核心建出来的适配器实例。"""
    return cls.instances[-1]


def test_registry_basics() -> None:
    print("[registry basics]")
    types = adapter_types()
    for expected in ("manual", "cli_subproc", "http_api", "mcp", "mock_recording"):
        check(f"已登记 {expected}", expected in types)
    built = create_adapter("mock_recording", {"id": "m"})
    check("按类型名实例化", isinstance(built, RecordingAdapter))
    check("adapter_type 自动对齐", built.adapter_type == "mock_recording")
    check("describe 不泄配置值", "executable" not in str(built.describe()))
    raises("未登记类型抛 UnknownAdapterError", lambda: create_adapter("nope"), UnknownAdapterError)
    raises("空类型名抛错", lambda: create_adapter(""), UnknownAdapterError)
    raises("非适配器类拒绝登记", lambda: register_adapter("bad", dict), ValueError)
    fallback = create_adapter_for({"id": "x", "name": "X"})
    check("缺省落到 manual", fallback.adapter_type == "manual")
    bound = create_adapter_for({"id": "y", "adapter": "mock_recording", "executable": "claude"})
    check("花名册 adapter 字符串决定实现", isinstance(bound, RecordingAdapter))
    check("整条配置透传给适配器", bound.config.get("executable") == "claude")


def test_base_contract_is_enforced() -> None:
    print("[base contract]")
    raises("抽象基类不能直接实例化", lambda: BaseWorkerAdapter(), TypeError)

    class MissingExecute(BaseWorkerAdapter):
        async def prepare_context(self, commission_id, briefing_packet):
            return {}

        async def cancel(self):
            return None

    raises("少一个方法就登记不上", lambda: register_adapter("missing_execute", MissingExecute), ValueError)
    raises("未知事件类型被拒", lambda: make_event("not_a_real_type"), ValueError)
    raises("非对象 artifact 被拒", lambda: artifact_event("nope"), ValueError)
    event = token_event(model="m", prompt_tokens=10, completion_tokens=2, ttft_ms=50)
    check("token 事件字段齐全",
          event["type"] == "token" and event["prompt_tokens"] == 10 and event["ttft_ms"] == 50)


def test_registry_driven_dispatch() -> None:
    print("[registry driven dispatch]")
    service = build_service([MOCK_AGENT])
    check("选将选中 mock",
          select_agent(service.evaluator.roster, "整理 mock 产出", {})[0]["id"] == "mock")
    commission = service.open("去把这个整理一下", "整理 mock 产出")
    service.dispatch(commission["id"], intent="整理 mock")
    check("阶段 briefing", service.commission(commission["id"])["status"] == "briefing")
    service.start(commission["id"])
    check("阶段 running", service.commission(commission["id"])["status"] == "running")

    result = asyncio.run(service.run_commission(commission["id"]))
    check("跑完进待验收", result["commission"]["status"] == "ready_to_report",
          result["commission"]["status"])
    check("没有失败原因", result["failure"] is None)
    check("三句话卡成形", result["commission"]["delivery"]["summary"].startswith("Mock 适配器"))

    adapter = last_instance()
    check("prepare_context 被调用过一次", len(adapter.prepared) == 1)
    packet = adapter.prepared[0][1]
    check("密件带 commission_id", packet.get("commission_id") == commission["id"])
    check("密件带观测网关地址", bool(packet.get("telemetry", {}).get("proxy_url")))
    check("跑完回收执行体", adapter.cancelled >= 1)

    usage = service.commission_usage(commission["id"])
    check("token 事件落成 span", usage["calls"] == 1, str(usage))
    check("span 带上 prompt_tokens", usage["prompt_tokens"] == 4321, str(usage))
    service.close()


def test_zero_touch_new_adapter_type() -> None:
    print("[zero touch new type]")
    # 关键断言：在已有登记之上再加一种全新类型，调度核心零改动即可派单。
    check("新类型已登记", "brand_new_type" in adapter_types())
    service = build_service([{
        "id": "fresh", "name": "全新工", "adapter": "brand_new_type",
        "strengths": ["全新"], "cost_tier": "cheap", "max_concurrent": 1,
        "eval_commands": ["required_fields"],
    }])
    commission = service.open("来个全新的活", "全新类型验证")
    service.dispatch(commission["id"], intent="全新")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("全新类型直接可跑", result["commission"]["status"] == "ready_to_report",
          result["commission"]["status"])
    check("调度核心无类型分支", "agent_type" not in open(
        ROOT / "server" / "assistant" / "dispatcher.py", encoding="utf-8").read())
    service.close()


def test_failure_paths() -> None:
    print("[failure paths]")
    # 鬼打墙：同一步骤签名连续重复 -> 熔断 -> cancel -> BLOCKED
    SpinningAdapter.default_events = [
        make_event("progress", step_idx=index, text="完全一样的进展") for index in range(5)
    ]
    service = build_service([{
        "id": "spinner", "name": "打转工", "adapter": "mock_spinning",
        "strengths": ["打转"], "cost_tier": "cheap", "max_concurrent": 1,
        "eval_commands": ["required_fields"],
    }])
    commission = service.open("让它打转", "鬼打墙熔断验证")
    service.dispatch(commission["id"], intent="打转")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("熔断后转 blocked", result["commission"]["status"] == "blocked",
          result["commission"]["status"])
    check("失败原因是 spinning", result["failure"] == "spinning")
    check("熔断前先 cancel 回收", last_instance(SpinningAdapter).cancelled >= 1)
    check("请示带批注", "打转" in result["commission"]["blocked_question"])
    service.close()

    # 执行体起不来：BLOCKED，不假装跑过
    service = build_service([{
        "id": "broken", "name": "坏执行体", "adapter": "mock_broken_start",
        "strengths": ["坏"], "cost_tier": "cheap", "max_concurrent": 1,
        "eval_commands": ["required_fields"],
    }])
    commission = service.open("起不来的活", "执行体缺失验证")
    service.dispatch(commission["id"], intent="坏")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("起不来转 blocked", result["commission"]["status"] == "blocked")
    check("失败原因记 prepare_failed", result["failure"] == "prepare_failed:AdapterError")
    service.close()

    # 花名册写了没登记的 adapter：BLOCKED 并说明，不静默失败
    service = build_service([{
        "id": "ghost", "name": "幽灵工", "adapter": "not_registered_at_all",
        "strengths": ["幽灵"], "cost_tier": "cheap", "max_concurrent": 1,
        "eval_commands": ["required_fields"],
    }])
    commission = service.open("幽灵的活", "未登记适配器验证")
    service.dispatch(commission["id"], intent="幽灵")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("未登记类型转 blocked", result["commission"]["status"] == "blocked")
    check("说明是适配器不可用", result["failure"] == "adapter_unavailable")
    check("请示里点名了类型", "not_registered_at_all" in result["commission"]["blocked_question"])
    service.close()

    # 执行体抛异常：转 BLOCKED，不把异常抛给调用方
    RecordingAdapter.raise_on_execute = RuntimeError("子进程炸了")
    service = build_service([MOCK_AGENT])
    commission = service.open("会炸的活", "执行体异常验证")
    service.dispatch(commission["id"], intent="整理 mock")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("异常也转 blocked", result["commission"]["status"] == "blocked")
    check("确实派给了 mock", result["failure"] != "adapter_unavailable")
    check("异常被记成失败原因", "RuntimeError" in (result["failure"] or ""))
    check("异常后仍然回收", last_instance().cancelled >= 1)
    RecordingAdapter.raise_on_execute = None
    service.close()


def test_manual_adapter_is_honest() -> None:
    print("[manual adapter]")
    service = build_service([])   # 三个内置 Agent 都是 adapter: manual
    commission = service.open("手动接一单", "manual 适配器验证")
    service.dispatch(commission["id"], intent="整理便笺标题")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("没有执行体就转 blocked", result["commission"]["status"] == "blocked")
    check("不伪造 artifact", result["verdict"] is None)
    check("请示说清是没有执行体，不用含糊的'没交差'",
          "没有配置执行体" in result["commission"]["blocked_question"])
    check("遥测里没有假 span", service.commission_usage(commission["id"])["calls"] == 0)
    service.close()


def test_delegate_drives_execution() -> None:
    print("[delegate drives execution]")
    # 正常委派：服务端自己驱动到终态，调用方不需要再触发一次。
    RecordingAdapter.instances = []
    service = build_service([MOCK_AGENT])
    result = asyncio.run(service.delegate("整理 mock 产出"))
    check("委派即跑完", result["status"] == "ready_to_report", result["status"])
    check("只建了一个执行体", len(RecordingAdapter.instances) == 1,
          str(len(RecordingAdapter.instances)))
    check("三句话卡落库",
          service.commission(result["commission_id"])["delivery"]["summary"].startswith("Mock 适配器"))
    service.close()

    # manual 缺省：明确阻塞等人工，不卡在"干活中"
    service = build_service([])
    result = asyncio.run(service.delegate("整理便笺标题"))
    check("没有执行体就转 blocked", result["status"] == "blocked", result["status"])
    check("请示说清没有执行体",
          "没有配置执行体" in service.commission(result["commission_id"])["blocked_question"])
    service.close()

    # 满载无将：仍然只留痕不外抛
    service = build_service([MOCK_AGENT])
    roster = dict(service.evaluator.roster)
    roster["agents"] = []
    service.dispatcher.roster = roster
    result = asyncio.run(service.delegate("整理 mock 产出"))
    check("没将可派转 blocked", result["status"] == "blocked", result["status"])
    service.close()


def test_concurrent_runs_do_not_duplicate() -> None:
    print("[concurrent runs]")
    RecordingAdapter.instances = []
    service = build_service([{"id": "slow", "name": "慢工", "adapter": "mock_slow",
                              "strengths": ["mock产出"], "cost_tier": "cheap", "max_concurrent": 2,
                              "eval_commands": ["required_fields", "artifacts_exist"]}])
    commission = service.open("并发的活", "同一票同时跑两次")
    service.dispatch(commission["id"], intent="mock产出")
    service.start(commission["id"])

    async def twice():
        return await asyncio.gather(service.run_commission(commission["id"]),
                                    service.run_commission(commission["id"]),
                                    return_exceptions=True)

    outcomes = asyncio.run(twice())
    check("只有一个执行体起身", len(RecordingAdapter.instances) == 1,
          str(len(RecordingAdapter.instances)))
    check("其中一个被租约挡下", any(isinstance(item, ValueError)
                                  and "already_running" in str(item) for item in outcomes),
          str([type(item).__name__ for item in outcomes]))
    check("票只被提交一次", service.commission(commission["id"])["status"] == "ready_to_report")
    service.close()


def test_partial_artifact_is_not_success() -> None:
    print("[partial artifact]")
    service = build_service([{"id": "half", "name": "半吊子", "adapter": "mock_artifact_then_error",
                              "strengths": ["mock产出"], "cost_tier": "cheap", "max_concurrent": 2,
                              "eval_commands": ["required_fields", "artifacts_exist"]}])
    commission = service.open("会炸的活", "先交差再报错")
    service.dispatch(commission["id"], intent="mock产出")
    service.start(commission["id"])
    result = asyncio.run(service.run_commission(commission["id"]))
    check("交了又报错不当成功", result["commission"]["status"] == "blocked",
          result["commission"]["status"])
    check("失败原因点名部分产物", (result["failure"] or "").startswith("partial_artifact"),
          str(result["failure"]))
    check("没有送进初审室", result["verdict"] is None)
    check("请示说明还有部分产物", "部分产物" in result["commission"]["blocked_question"])
    service.close()


def test_adapter_bindings_are_readable() -> None:
    print("[adapter bindings]")
    service = build_service([MOCK_AGENT])
    bindings = {item["agent_id"]: item for item in service.adapter_bindings()}
    check("mock 绑定可读", bindings.get("mock", {}).get("adapter") == "mock_recording")
    check("内置 Agent 仍是 manual", bindings.get("scribe", {}).get("adapter") == "manual")
    check("类型清单可读", "mock_recording" in service.adapter_types())
    raises("查不存在的 Agent 抛 KeyError", lambda: service.dispatcher.adapter_for("nope"), KeyError)
    service.close()


def main() -> int:
    for test in (test_registry_basics, test_base_contract_is_enforced,
                 test_registry_driven_dispatch, test_zero_touch_new_adapter_type,
                 test_failure_paths, test_manual_adapter_is_honest,
                 test_delegate_drives_execution, test_concurrent_runs_do_not_duplicate,
                 test_partial_artifact_is_not_success,
                 test_adapter_bindings_are_readable):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
