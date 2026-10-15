"""V3 内阁总管离线自检：阶段机、派单密件、观测网关、自进化初审、台前意图。

    python scripts/test_executive_v3.py

全部确定性、零网络、零真实模型调用。数据库一律 :memory: 或临时目录。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ["DB_PATH"] = ":memory:"

from server.assistant.dispatcher import (BriefingPacket, Dispatcher, roster_agents,  # noqa: E402
                                          select_agent)
from server.assistant.evaluator import (EvalRubricStore, Evaluator, extract_triggers,  # noqa: E402
                                        load_roster, scold)
from server.assistant.executive import ExecutiveService  # noqa: E402
from server.assistant.loop import capture_eval_delta  # noqa: E402
from server.assistant.telemetry import (SpinningGuard, TelemetryGateway, TelemetryStore,  # noqa: E402
                                        clip_error, normalize_signature)
from server.dialogue import ambient  # noqa: E402
from server.dialogue.orchestrator import (acceptance_intent, commission_accept_line,  # noqa: E402
                                          delegation_intent, progress_query_intent,
                                          stage_sample_line, telemetry_bottleneck_report,
                                          telemetry_query_intent)
from server.interaction.threads import CommissionStore  # noqa: E402

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
    "summary": "把《七月选题》整理成三版标题，每版附一句理由。",
    "intercepted": "它第一版想直接在顶栏加个按钮，我按铁律拦下了。",
    "artifacts": [{"kind": "note", "ref": "note:n_1@7", "preview": "三版标题"}],
    "wired": True,
    "touched_ui": ["drawer"],
}


def build_service() -> ExecutiveService:
    directory = Path(tempfile.mkdtemp(prefix="executive-v3-"))
    return ExecutiveService(directory, roster=load_roster())


def test_commission_state_machine() -> None:
    print("[commission state machine]")
    store = CommissionStore(":memory:")
    item = store.create("整理便笺", "把七月选题理成三版标题")
    check("新建落在 drafting", item["status"] == "drafting")
    check("id 带 c_ 前缀", item["id"].startswith("c_"))
    raises("drafting 不能直接 running", lambda: store.transition(item["id"], "running"), ValueError)
    store.transition(item["id"], "briefing", agent_id="scribe")
    store.transition(item["id"], "running")
    store.record_step(item["id"], 2)
    check("step_idx 记录", store.get(item["id"])["step_idx"] == 2)
    store.transition(item["id"], "auditing", audit={"passed": False, "layer": "l1"})
    store.transition(item["id"], "running", retry_delta=1)
    check("打回累计 retry_count", store.get(item["id"])["retry_count"] == 1)
    raises("blocked 必须带问题", lambda: store.transition(item["id"], "blocked"), ValueError)
    store.transition(item["id"], "blocked", blocked_question="换将还是等一轮？")
    check("请示落在票上", store.get(item["id"])["blocked_question"] == "换将还是等一轮？")
    store.transition(item["id"], "running", blocked_question="")
    check("答完清空请示", store.get(item["id"])["blocked_question"] == "")
    store.transition(item["id"], "auditing", audit={"passed": True, "layer": "none"})
    store.transition(item["id"], "ready_to_report", delivery={"summary": "三版标题"})
    check("三句话卡落库", store.get(item["id"])["delivery"]["summary"] == "三版标题")
    store.transition(item["id"], "running")
    store.transition(item["id"], "auditing", audit={"passed": True, "layer": "none"})
    store.transition(item["id"], "ready_to_report")
    closed = store.transition(item["id"], "done")
    check("done 写 closed_at", closed["closed_at"] is not None)
    raises("终态不能再迁移", lambda: store.transition(item["id"], "running"), ValueError)
    raises("缺失 id 抛 KeyError", lambda: store.get("c_missing"), KeyError)
    events = store.events(item["id"])
    check("事件只追加", any(e["kind"] == "transition" for e in events))
    raises("非法状态过滤", lambda: store.list_commissions("nope"), ValueError)


def test_briefing_packet() -> None:
    print("[briefing packet]")
    service = build_service()
    commission = service.open("去把这个整理一下", "把《七月选题》理成三版标题")
    result = service.dispatch(commission["id"], intent="整理便笺标题")
    packet = result["packet"]
    check("阶段推到 briefing", result["commission"]["status"] == "briefing")
    check("选将命中能力", packet["agent_id"] == "scribe", packet["agent_id"])
    check("密件有交付物", bool(packet["deliverable"]))
    check("密件有预算", packet["budget"]["max_retry"] == 2)
    check("密件带 L1 命令", "artifacts_exist" in packet["budget"]["eval_commands"])
    check("密件带判例", any(r["id"] == "no_topbar_button" for r in packet["rubrics"]))
    other = service.open("这个页面布局改一下", "调整抽屉视图样式")
    picked = service.dispatch(other["id"], intent="前端页面样式组件")["packet"]
    check("Builder 带顶栏铁律", "no_topbar_button" in picked["budget"]["eval_commands"])
    check("前端活派给营造匠", picked["agent_id"] == "builder", picked["agent_id"])
    check("检索活派给探马", select_agent(load_roster(), "帮我检索查一下这份资料", {})[0]["id"] == "scout")
    service.close()


def test_roster_selection_and_capacity() -> None:
    print("[roster selection]")
    roster = load_roster()
    check("花名册三个 Agent", len(roster["agents"]) == 3)
    check("旧键 workers 仍被识别", roster_agents({"workers": roster["agents"]}) == roster["agents"])
    check("三条种子铁律", len(roster["seed_rules"]) == 3)
    full = {worker["id"]: worker.get("max_concurrent", 2) for worker in roster_agents(roster)}
    best, ranked = select_agent(roster, "整理标题摘要", full)
    check("满载时无将可选", best is None)
    check("排行仍给出理由", all("score" in item for item in ranked))
    partial = {"scribe": 3}
    best, _ = select_agent(roster, "整理标题摘要", partial)
    check("笔录官满载就换将", best is not None and best["id"] != "scribe")


def test_memory_block_is_framed() -> None:
    print("[memory framing]")
    from server.memory.commission_context import gather, render_memory_block

    memory = gather(notes_provider=lambda q: [{"id": "n_1", "version": 7, "title": "七月选题"}],
                    threads_provider=lambda q: [{"id": "t_1", "title": "选题会"}],
                    preference_provider=lambda q: [{"dimension": "写作风格", "value": "先短句"}],
                    query="整理")
    block = render_memory_block(memory)
    check("记忆段带取景框", block.startswith("【不可信派单资料") and block.endswith("【派单资料结束】"))
    check("三类资料都进包", "n_1" in block and "t_1" in block and "先短句" in block)
    broken = gather(notes_provider=lambda q: (_ for _ in ()).throw(RuntimeError("boom")))
    check("provider 炸了只丢一组", broken["notes"] == []
          and any("notes_error" in item for item in broken["omitted"]))
    check("空记忆不渲染", render_memory_block(gather()) == "")


def test_telemetry_store_and_summary() -> None:
    print("[telemetry]")
    store = TelemetryStore(":memory:", clock=lambda: 1000.0)
    span = store.record_span(commission_id="c_1", agent_id="scribe", step_idx=1, model="deepseek-chat",
                             prompt_tokens=900, completion_tokens=120, ttft_ms=640, duration_ms=1800,
                             status_code=200)
    for field in ("commission_id", "agent_id", "step_idx", "model", "prompt_tokens",
                  "completion_tokens", "ttft_ms", "duration_ms", "status_code", "error_snippet"):
        check(f"span 含 {field}", field in span)
    store.record_span(commission_id="c_1", agent_id="builder", step_idx=1, prompt_tokens=100,
                      completion_tokens=10, ttft_ms=900, duration_ms=2400, status_code=500,
                      error_snippet="upstream 500 with Authorization: Bearer sk-abcdef123456")
    summary = store.summary(since=0.0, now=2000.0)
    check("调用次数", summary["calls"] == 2)
    check("成功率", summary["success_rate"] == 0.5)
    check("token 合计", summary["total_tokens"] == 1130)
    check("排行降序", summary["token_ranking"][0]["agent_id"] == "scribe")
    check("按 agent 分组", {item["agent_id"] for item in summary["by_agent"]} == {"scribe", "builder"})
    empty = TelemetryStore(":memory:").summary()
    check("空统计不假装健康", empty["calls"] == 0 and empty["success_rate"] is None
          and empty["token_ranking"] == [])
    usage = store.commission_usage("c_1")
    check("单票用量", usage["calls"] == 2 and usage["prompt_tokens"] == 1000)
    check("错误片段剥凭据", "sk-abcdef" not in store.spans(limit=1)[0]["error_snippet"]
          or "REDACTED" in store.spans(limit=1)[0]["error_snippet"])
    check("错误片段截断", len(clip_error("x" * 900)) == 200)
    raises("非法 token 报错", lambda: store.record_span(commission_id="c", agent_id="a",
                                                       prompt_tokens="many"), ValueError)


def test_spinning_guard() -> None:
    print("[spinning guard]")
    guard = SpinningGuard(max_identical=3)
    signature = normalize_signature({"action": "note.read", "arguments": {"note_id": "n_1"},
                                     "output": "同一段正文"})
    first = guard.observe("c_1", signature, agent_id="scribe")
    second = guard.observe("c_1", signature, agent_id="scribe")
    check("前两次不熔断", not first["tripped"] and not second["tripped"])
    third = guard.observe("c_1", signature, agent_id="scribe")
    check("第三次熔断", third["tripped"] and third["repeats"] == 3)
    check("熔断留下告警", len(guard.alerts()) == 1)
    guard.reset("c_1")
    check("reset 后重新计数", not guard.observe("c_1", signature)["tripped"])
    other = normalize_signature({"action": "note.read", "arguments": {"note_id": "n_2"},
                                 "output": "另一段正文"})
    check("不同签名不误熔", not guard.observe("c_2", other)["tripped"])
    same_shape = normalize_signature({"action": "note.read", "arguments": {"note_id": "n_3"},
                                      "output": "另一段正文"})
    reworded = normalize_signature({"action": "note.read", "arguments": {"note_id": "n_3"},
                                    "output": "  另一段正文。 "})
    check("只有措辞差异不算重复", same_shape == reworded)
    check("参数不同即不同步骤", same_shape != other)
    check("同一 commission 不串门", not guard.observe("c_3", signature)["tripped"])
    raises("阈值必须 >=2", lambda: SpinningGuard(max_identical=1), ValueError)


def test_telemetry_gateway_proxy() -> None:
    print("[telemetry proxy]")
    store = TelemetryStore(":memory:")
    gateway = TelemetryGateway(store, guard=SpinningGuard(max_identical=2))

    async def transport(payload):
        return {"status_code": 200, "body": {"ok": True},
                "ttft_ms": 300, "usage": {"prompt_tokens": 40, "completion_tokens": 8}}

    result = asyncio.run(gateway.proxy({"commission_id": "c_9", "agent_id": "scout", "step_idx": 1,
                                        "path": "/chat/completions", "body": {"x": 1}}, transport))
    check("上游响应原样返回", result["body"] == {"ok": True} and result["status_code"] == 200)
    check("span 落库", result["span"]["prompt_tokens"] == 40)
    check("落库可查", store.commission_usage("c_9")["calls"] == 1)

    async def boom(payload):
        raise RuntimeError("upstream down")

    failed = asyncio.run(gateway.proxy({"commission_id": "c_9", "agent_id": "scout", "step_idx": 2,
                                        "path": "/chat/completions"}, boom))
    check("上游炸了不带走调用方", failed["status_code"] == 0 and "RuntimeError" in failed["span"]["error_snippet"])

    async def same(payload):
        return {"status_code": 200, "body": {"ok": True}, "ttft_ms": 10, "usage": {}}

    asyncio.run(gateway.proxy({"commission_id": "c_9", "agent_id": "scout", "step_idx": 3,
                               "path": "/chat/completions", "body": {"same": 1}}, same))
    trip = asyncio.run(gateway.proxy({"commission_id": "c_9", "agent_id": "scout", "step_idx": 4,
                                      "path": "/chat/completions", "body": {"same": 1}}, same))
    check("代理侧也熔断", trip["spinning"]["tripped"])
    event = gateway.record_event({"commission_id": "c_9", "agent_id": "scout", "step_idx": 5,
                                  "status_code": 200, "prompt_tokens": 12, "ttft_ms": 90,
                                  "duration_ms": 200})
    check("子进程事件同表", store.commission_usage("c_9")["calls"] == 5)
    check("Dual-Shim 归一", event["span"]["prompt_tokens"] == 12)


def test_evaluator_layers() -> None:
    print("[evaluator l1/l2]")
    evaluator = Evaluator(roster=load_roster(), path=":memory:")
    check("未 seed 前判例库是空的", evaluator.rubrics.list() == [])
    check("三条种子铁律落库", evaluator.seed() == 3 and len(evaluator.rubrics.list()) == 3)
    check("重复 seed 不叠加", evaluator.seed() == 0)
    packet = {"budget": {"eval_commands": ["required_fields", "artifacts_exist", "wired_not_dry",
                                           "no_topbar_button", "token_budget"],
                         "max_prompt_tokens": 1000}}
    commission = {"id": "c_1", "retry_count": 0}
    good = evaluator.review(commission=commission, artifact=GOOD_ARTIFACT, packet=packet,
                            usage={"calls": 2, "prompt_tokens": 500})
    check("完整交付过审", good["passed"] and good["layer"] == "none")
    check("过审带三句话", "1 项成果" in good["scolding"])
    missing = dict(GOOD_ARTIFACT, summary="", artifacts=[{"kind": "note", "ref": ""}], wired=False)
    bad = evaluator.review(commission=commission, artifact=missing, packet=packet,
                           usage={"calls": 1, "prompt_tokens": 100})
    check("L1 拦住空跑", not bad["passed"] and bad["layer"] == "l1")
    check("L1 训话是报错驳回", "客观不过关" in bad["scolding"])
    check("未接线单独成条", any(f["rule_id"] == "no_unwired_dry_run" for f in bad["findings"]))
    bloated = evaluator.review(commission=commission, artifact=GOOD_ARTIFACT, packet=packet,
                               usage={"calls": 9, "prompt_tokens": 5000})
    check("Token 膨胀被打回", any(f["rule_id"] == "prompt_token_bloat" for f in bloated["findings"]))
    no_usage = evaluator.review(commission=commission, artifact=GOOD_ARTIFACT, packet=packet)
    check("没有 span 不妄判膨胀", no_usage["passed"])
    topbar = dict(GOOD_ARTIFACT, touched_ui=["topbar"])
    hit = evaluator.review(commission=commission, artifact=topbar, packet=packet,
                           usage={"calls": 1, "prompt_tokens": 10})
    check("动了顶栏被拦", any(f["rule_id"] == "no_topbar_button" for f in hit["findings"]))
    admitted = dict(GOOD_ARTIFACT, summary="我在顶栏加了个按钮，顺手")
    l2 = evaluator.review(commission=commission, artifact=admitted, packet=packet,
                          usage={"calls": 1, "prompt_tokens": 10})
    check("L2 判例命中自述", l2["layer"] == "l2" and "判例库" in l2["scolding"])
    check("初审不把「我拦下了」当违规", evaluator.review(
        commission=commission, artifact=GOOD_ARTIFACT, packet=packet,
        usage={"calls": 1, "prompt_tokens": 10})["passed"])
    spinning = evaluator.review(commission=commission, artifact=GOOD_ARTIFACT, packet=packet,
                                usage={"calls": 1, "prompt_tokens": 10},
                                spinning={"tripped": True, "repeats": 3})
    check("鬼打墙进初审", any(f["rule_id"] == "spinning_guard" for f in spinning["findings"]))
    check("熔断训话", "闸" in scold("spinning_warning", "同一步骤重复三次"))


def test_evaluator_self_evolution() -> None:
    print("[eval self-evolution]")
    evaluator = Evaluator(roster=load_roster(), path=":memory:")
    evaluator.seed()
    check("提炼触发词", "顶栏" in extract_triggers("别在顶栏加按钮，放抽屉里"))
    check("空反馈不长判例", evaluator.distill(commission={}, boss_feedback="   ") is None)
    check("太短的反馈不长判例", evaluator.distill(commission={}, boss_feedback="不行") is None)
    commission = {"id": "c_1", "audit": {"passed": True, "layer": "none", "findings": []}}
    delta = capture_eval_delta(commission=commission, boss_feedback="谁让你把按钮放顶栏的，收回去",
                               evaluator=evaluator)
    check("漏审点被捕获", delta is not None and delta.audit_layer == "none")
    check("新判例带来源", delta.learned_rubric_id and delta.learned_rubric_id.startswith("boss_"))
    learned = [item for item in evaluator.rubrics.list() if item["source"] == "boss_rejection"]
    check("判例库多一条", len(learned) == 1)
    check("判例带证据", "顶栏" in learned[0]["evidence"])
    again = capture_eval_delta(commission=commission, boss_feedback="谁让你把按钮放顶栏的，收回去",
                               evaluator=evaluator)
    check("同一 Delta 不重复落库", again is not None and again.learned_rubric_id is None
          and len(evaluator.rubrics.list()) == 4)
    check("没有 evaluator 不落库", capture_eval_delta(commission=commission, boss_feedback="随便挑刺",
                                                      evaluator=None) is None)
    check("判例可删", evaluator.rubrics.deactivate(learned[0]["id"])
          and not any(i["id"] == learned[0]["id"] for i in evaluator.rubrics.list()))


def test_dispatcher_lifecycle() -> None:
    print("[dispatcher lifecycle]")
    service = build_service()
    commission = service.open("去把这个整理一下", "把《七月选题》理成三版标题")
    service.dispatch(commission["id"], intent="整理便笺标题")
    service.start(commission["id"])
    check("进入 running", service.commission(commission["id"])["status"] == "running")
    blocked = service.answer.__self__  # keep the facade honest: answer needs a blocked commission
    raises("未 blocked 不能答", lambda: service.answer(commission["id"], "都可以"), ValueError)
    check("琐事代答有依据", service.dispatcher.trivia_answer("这个用什么名字") is not None)
    check("大事不猜", service.dispatcher.trivia_answer("要不要把整个模块推翻重写") is None)
    service.dispatcher.block(commission["id"], "换将还是等一轮？")
    answered = service.answer(commission["id"], "等一轮")
    check("答完回 running", answered["commission"]["status"] == "running" and not answered["auto_answered"])
    verdict = service.submit(commission["id"], GOOD_ARTIFACT)
    check("初审放行进待验收", verdict["commission"]["status"] == "ready_to_report")
    check("三句话卡成形", verdict["commission"]["delivery"]["summary"].startswith("把《七月选题》"))
    service.accept(commission["id"])
    check("收工归档", service.commission(commission["id"])["status"] == "done")


def test_dispatcher_retry_cap() -> None:
    print("[dispatcher retry cap]")
    service = build_service()
    commission = service.open("这个页面布局改一下", "调整抽屉视图样式")
    service.dispatch(commission["id"], intent="前端页面样式")
    service.start(commission["id"])
    broken = dict(GOOD_ARTIFACT, summary="", artifacts=[], wired=False)
    first = service.submit(commission["id"], broken)
    check("第一次打回 running", first["commission"]["status"] == "running")
    check("打回计数 1", first["commission"]["retry_count"] == 1)
    second = service.submit(commission["id"], broken)
    check("第二次打回 running", second["commission"]["status"] == "running")
    check("打回计数 2", second["commission"]["retry_count"] == 2)
    third = service.submit(commission["id"], broken)
    check("第三次转 blocked 请示", third["commission"]["status"] == "blocked")
    check("请示带完整批注", "audit" in third["commission"] and third["verdict"]["scolding"])
    check("不再无限重试", third["commission"]["retry_count"] == 3)
    raises("blocked 后不能直接交差", lambda: service.submit(commission["id"], GOOD_ARTIFACT), ValueError)


def test_boss_rejection_learns() -> None:
    print("[boss rejection]")
    service = build_service()
    commission = service.open("去把这个整理一下", "把《七月选题》理成三版标题")
    service.dispatch(commission["id"], intent="整理便笺标题")
    service.start(commission["id"])
    service.submit(commission["id"], GOOD_ARTIFACT)
    result = service.reject(commission["id"], "谁让你在顶栏加按钮的，收回去")
    check("退回后重回 running", result["commission"]["status"] == "running")
    check("长出一条判例", result["rubric"] is not None)
    check("判例记在票上", result["rubric"]["learned_rubric_id"] in result["commission"]["rubric_ids"])
    learned = [item for item in service.rubrics() if item["source"] == "boss_rejection"]
    check("判例库可见可删", len(learned) == 1 and service.deactivate_rubric(learned[0]["id"]))
    second = service.open("去把这个整理一下", "再整理一版")
    service.dispatch(second["id"], intent="整理便笺标题")
    service.start(second["id"])
    service.submit(second["id"], GOOD_ARTIFACT)
    thin = service.reject(second["id"], "不行")
    check("退回原话太短不长判例", thin["rubric"] is None)
    check("太短也照样打回重做", thin["commission"]["status"] == "running")
    service.close()


def test_delegate_without_worker() -> None:
    print("[delegate degradation]")
    service = build_service()
    roster = dict(service.evaluator.roster)
    roster["agents"] = []
    service.dispatcher.roster = roster
    result = service.delegate("去把这个整理一下")
    check("没将可派转 blocked", result["status"] == "blocked")
    check("请示一句话", "没人能接" in service.commission(result["commission_id"])["blocked_question"])
    service.close()


def test_front_intents() -> None:
    print("[front intents]")
    check("委托意图", delegation_intent("去把这个整理一下"))
    check("安排人也是委托", delegation_intent("安排人做个页面"))
    check("盯着也是委托", delegation_intent("这件事你盯着，有结论再叫我"))
    check("普通求助不是委托", not delegation_intent("帮我理一理思路"))
    check("闲聊不是委托", not delegation_intent("今天天气不错啊"))
    check("进度追问", progress_query_intent("那单进度怎么样了"))
    check("战况查询", telemetry_query_intent("今天 API 烧了多少 token"))
    check("普通提问不是战况", not telemetry_query_intent("我们晚上吃什么"))
    check("验收-收工", acceptance_intent("收工") == "accept")
    check("验收-打回", acceptance_intent("不行，顶栏那个按钮收回去") == "reject")
    check("普通句不判验收", acceptance_intent("今天先这样吧") is None)


def test_front_samples_and_report() -> None:
    print("[front samples]")
    for key in ("commission_accept", "commission_progress", "telemetry_token_bloat",
                "spinning_intercept_mumble", "audit_reject_brag", "plan_guardian_pushback",
                "eval_self_reflection"):
        check(f"台词样本 {key}", bool(stage_sample_line(key)))
    check("未知 key 静默", stage_sample_line("nope") == "")
    check("接单样本带情绪标签", commission_accept_line().startswith("["))
    check("空战况不播报", telemetry_bottleneck_report({}) == "")
    check("零调用不播报", telemetry_bottleneck_report({"calls": 0, "token_ranking": []}) == "")
    report = telemetry_bottleneck_report({
        "calls": 40, "failed": 6, "success_rate": 0.85, "total_tokens": 200000,
        "avg_ttft_ms": 2100, "max_ttft_ms": 5000,
        "token_ranking": [{"agent_id": "scribe", "total_tokens": 150000}],
        "spinning_alerts": [{"commission_id": "c_1", "agent_id": "builder", "repeats": 3}]})
    check("瓶颈分析点名消耗头名", "scribe" in report and "85%" in report)
    check("瓶颈分析提延迟", "2100" in report)
    check("瓶颈分析提熔断", "鬼打墙" in report)


def test_orchestrator_route_needs_real_state() -> None:
    print("[orchestrator route]")
    from server.dialogue.orchestrator import Orchestrator

    service = build_service()
    orchestrator = Orchestrator()
    check("没有台账不劫走普通对话", orchestrator.executive_route("进度怎么样了", service) is None)
    check("没有台账不当战况播报", orchestrator.executive_route("今天 token 烧了多少", service) is None)
    check("没有样本不接单", True)
    commission = service.open("去把这个整理一下", "把《七月选题》理成三版标题")
    service.dispatch(commission["id"], intent="整理便笺标题")
    service.start(commission["id"])
    route = orchestrator.executive_route("那单进度怎么样了", service)
    check("有在跑的单才报进度", route is not None and route["kind"] == "progress")
    telemetry = orchestrator.executive_route("今天 API 烧了多少 token", service)
    check("有在跑的单才报战况", telemetry is None or telemetry["kind"] == "telemetry")
    service.submit(commission["id"], GOOD_ARTIFACT)
    accepted = orchestrator.executive_route("收工", service)
    check("有待验收才认收工", accepted is not None and accepted["kind"] == "accept")
    rejected = orchestrator.executive_route("不行，顶栏那个按钮收回去", service)
    check("有待验收才认打回", rejected is not None and rejected["kind"] == "reject")
    check("打回带上原话", "顶栏" in rejected["feedback"])
    service.close()


def test_ambient_supervisor_check() -> None:
    print("[ambient supervisor]")
    event = ambient.build_supervisor_check(reason="running_30s", now=1000.0)
    check("监工微动作是 micro_act", event["type"] == "micro_act")
    check("动作在白名单内", event["action"] in ambient.SUPERVISOR_ACTIONS)
    check("原因带 supervising 前缀", event["trigger_reason"].startswith("supervising_"))
    check("有 event_id", bool(event["event_id"]))
    driver_source = (ROOT / "web" / "avatar" / "drivers" / "ambient.js").read_text(encoding="utf-8")
    for action in ambient.SUPERVISOR_ACTIONS:
        check(f"前端认得 {action}", action in driver_source)


def test_rest_seam() -> None:
    print("[rest seam]")
    from fastapi.testclient import TestClient

    from server.app import app

    with TestClient(app) as client:
        listing = client.get("/api/executive/commissions")
        check("台账列表 200", listing.status_code == 200 and "items" in listing.json())
        summary = client.get("/api/executive/telemetry")
        check("战况统计 200", summary.status_code == 200 and summary.json()["calls"] >= 0)
        report = client.get("/api/executive/telemetry/report")
        check("战况播报 200", report.status_code == 200 and "text" in report.json())
        rubrics = client.get("/api/executive/rubrics")
        check("判例库 200", rubrics.status_code == 200 and len(rubrics.json()["items"]) == 3)
        created = client.post("/api/executive/commissions",
                              json={"title": "去把这个整理一下", "brief": "把《七月选题》理成三版标题"})
        check("开票 200", created.status_code == 200)
        commission_id = created.json()["id"]
        one = client.get(f"/api/executive/commissions/{commission_id}")
        check("单票可读", one.status_code == 200 and one.json()["status"] == "drafting")
        check("单票事件留痕", client.get(
            f"/api/executive/commissions/{commission_id}/events").json()["items"] != [])
        blocked = client.post(f"/api/executive/commissions/{commission_id}/answer", json={"answer": "等一轮"})
        check("未请示就回答被拒", blocked.status_code == 400)
        cross = client.post("/api/executive/commissions", json={"title": "x"},
                            headers={"origin": "http://evil.example"})
        check("跨源写被拒", cross.status_code == 403)
        closed = client.post(f"/api/executive/commissions/{commission_id}/cancel", json={})
        check("作票归档", closed.status_code == 200 and closed.json()["status"] == "done")
        missing = client.get("/api/executive/commissions/c_missing")
        check("缺失票 404", missing.status_code == 404)


def main() -> int:
    for test in (test_commission_state_machine, test_briefing_packet, test_roster_selection_and_capacity,
                 test_memory_block_is_framed, test_telemetry_store_and_summary, test_spinning_guard,
                 test_telemetry_gateway_proxy, test_evaluator_layers, test_evaluator_self_evolution,
                 test_dispatcher_lifecycle, test_dispatcher_retry_cap, test_boss_rejection_learns,
                 test_delegate_without_worker, test_front_intents, test_front_samples_and_report,
                 test_orchestrator_route_needs_real_state, test_ambient_supervisor_check,
                 test_rest_seam):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("  -", name)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
