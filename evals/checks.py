"""回归评测的 custom 判定函数。

被 scripts/run_evals.py 按 `evals.checks:<函数名>` 动态调用。
约定：fn(case: dict, state: dict) -> tuple[bool, str]
state 由 runner 准备：{"replies": [...], "prompt": str}，预置记忆已由 runner 种好。

所有 server 模块都在函数体内懒加载：runner 必须先把 DB_PATH 钉成 :memory:，
这里 import 才不会碰到真实数据库。
"""
from __future__ import annotations


def _valid_emotions() -> set[str]:
    from server.config import persona_cfg
    return set((persona_cfg().get("tts_emotions") or {}).keys())


def fewshot_emotion_tags(case, state):
    """每条 Assistant few-shot 示例都以合法 [情绪] 标签开头。

    解析复用运行时的 _EMOTION_TAG（orchestrator 喂给 TTS 前用的同一套），
    合法集合从 config/persona.yaml 的 tts_emotions 读取，两边都不硬编码。
    """
    from server.dialogue.orchestrator import _EMOTION_TAG
    from server.persona import demo_turns

    valid = _valid_emotions()
    bad = []
    for i, turn in enumerate(demo_turns()):
        if turn["role"] != "assistant":
            continue
        match = _EMOTION_TAG.match(turn["content"])
        if not match:
            bad.append(f"示例#{i} 缺少 [情绪] 前缀")
        elif match.group(1) not in valid:
            bad.append(f"示例#{i} 标签 {match.group(1)!r} 不在 tts_emotions 里")
    return (not bad), "；".join(bad) or "全部示例带合法标签"


def tier_assembly_order(case, state):
    """三档置信度按 明确说过 -> 我的推测 -> 我不确定的 顺序出现在检索块里。"""
    block = state.get("memory_block") or ""
    markers = ["【手动保存的】", "【我的推测】", "【我不确定的】"]
    positions = [block.find(m) for m in markers]
    missing = [m for m, p in zip(markers, positions) if p < 0]
    if missing:
        return False, f"检索块缺档位标记 {missing}"
    ok = positions == sorted(positions)
    return ok, f"位置 {dict(zip(markers, positions))}"


def hard_delete(case, state):
    """物理硬删除：行没了、检索空了、历史清了，SQLite 完整性检查通过（索引无残留）。"""
    from server.memory.manager import manager
    from server.memory.store import store

    mem_id = store.upsert(kind="fact", subject="user", key="eval:hard-delete",
                          value="用户周五要去杭州出差", source="stated", importance=0.8)
    manager.forget(mem_id)
    problems = []
    if store.count() != 0:
        problems.append(f"删除后还有 {store.count()} 行")
    if manager.context_for("杭州出差") != "":
        problems.append("删除后检索仍能命中")
    if store.history_for_key("eval:hard-delete"):
        problems.append("删除后历史链仍有残留")
    # 硬删除若留下悬空索引项，integrity_check 会报出来。
    rows = store._conn.execute("PRAGMA integrity_check").fetchall()
    if [(r[0],) for r in rows] != [("ok",)]:
        problems.append(f"integrity_check={rows}")
    return (not problems), "；".join(problems) or "行/检索/历史/索引全部干净"


async def fastloop_nonblocking(case, state):
    """复杂委托走慢环时，快环（respond）立即返回语音确认，不被慢 Worker 拖住。

    用事件顺序断言（不用墙钟）：worker_start 之后，fast_return 必须先于 worker_done。
    Mock 慢 Worker 复用 test_agent_adapter_ext.py 的登记模式。
    """
    import asyncio
    import copy
    import uuid
    from pathlib import Path

    # 沙盘里 tempfile.mkdtemp 建的目录 ACL 对 sqlite 不可写，os.makedirs 正常。
    scratch = Path(__file__).resolve().parent.parent / "tmp" / f"eval-fastloop-{uuid.uuid4().hex[:8]}"
    scratch.mkdir(parents=True, exist_ok=True)

    from server.assistant.adapters import register_adapter
    from server.assistant.adapters.base import BaseWorkerAdapter, artifact_event, make_event
    from server.assistant.evaluator import load_roster
    from server.assistant.executive import ExecutiveService
    from server.dialogue.orchestrator import Orchestrator

    events: list[str] = []
    gate = asyncio.Event()  # 慢 Worker 在此闸门前等待，快环返回后才放行

    class EvalSlowAdapter(BaseWorkerAdapter):
        adapter_type = "eval_slow"

        async def prepare_context(self, commission_id, briefing_packet):
            return {"commission_id": commission_id}

        async def execute(self, task_spec):
            events.append("worker_start")
            try:
                await asyncio.wait_for(gate.wait(), timeout=10.0)  # 仅作死锁兜底，不是性能阈值
            except asyncio.TimeoutError:
                events.append("worker_deadlock")
            events.append("worker_done")
            yield make_event("progress", step_idx=1, text="eval 慢活进行中")
            yield artifact_event({"summary": "eval 慢活干完了", "intercepted": "",
                                  "artifacts": [], "wired": True, "touched_ui": []})

        async def cancel(self):
            return None

    register_adapter("eval_slow", EvalSlowAdapter)
    roster = copy.deepcopy(load_roster())
    roster["agents"] = [a for a in roster.get("agents", []) if a.get("id") != "evalslow"]
    roster["agents"].append({"id": "evalslow", "name": "eval 慢工", "adapter": "eval_slow",
                             "strengths": ["evalslow", "汇总材料"], "cost_tier": "cheap",
                             "max_concurrent": 1, "eval_commands": ["required_fields"]})
    service = ExecutiveService(scratch, roster=roster)

    async def scenario():
        slow = asyncio.create_task(service.delegate("evalslow 汇总材料"))
        await asyncio.sleep(0)  # 让慢环起身
        orch = Orchestrator()
        result = await orch.respond(session_id="eval-fastloop", user_text=case["turns"][0])
        events.append("fast_return")
        gate.set()  # 快环已返回，放行慢环
        await slow
        return result

    result = await scenario()
    service.close()

    problems = []
    if "worker_start" not in events or "worker_done" not in events:
        problems.append(f"慢 Worker 事件不全: {events}")
    if "fast_return" not in events:
        problems.append("快环没有返回")
    if not problems and events.index("fast_return") > events.index("worker_done"):
        problems.append(f"快环被慢环拖住: {events}")
    if not (result.text or "").strip():
        problems.append("快环返回了空文本")
    return (not problems), f"事件序 {events}"


def no_fabricated_shared_experience(case, state):
    """live：回复不得断言从未发生的共同经历（第一版正则，有误判风险，原始回复已落盘）。"""
    import re
    fabricated = re.compile(r"我记得我们|我们之前一起|上次我们|咱们之前|你以前说过")
    bad = [r for r in state.get("replies", []) if fabricated.search(r)]
    return (not bad), f"{len(bad)} 条回复疑似编造共同经历" if bad else "未发现编造性指代"


def all_replies_start_with_valid_tag(case, state):
    """live：每轮回复都以 config 里登记的合法 [情绪] 标签开头。"""
    from server.dialogue.orchestrator import _EMOTION_TAG

    valid = _valid_emotions()
    replies = state.get("replies", [])
    bad = []
    for i, reply in enumerate(replies):
        match = _EMOTION_TAG.match(reply)
        if not match:
            bad.append(f"第{i+1}轮无标签前缀")
        elif match.group(1) not in valid:
            bad.append(f"第{i+1}轮标签 {match.group(1)!r} 非法")
    return (not bad), "；".join(bad) or f"{len(replies)} 轮全部带合法标签"
