"""幕后慢环：把模糊意图组成交派密件（Briefing Packet），按花名册选将，
推进督办阶段机。它从不直接和用户说话——前台怎么说由 orchestrator 决定。

三条底线：

1. **选将只看花名册上写得下的东西。** 能力匹配、当前负载、成本档位。
   没有"最近表现分"这种无据可查的指标。
2. **满载不接单。** 同一位 Worker 在手的督办数到顶，就换将；没有将可换才问老板。
3. **琐事代答要有依据。** 花名册 `trivia_policy` 或已有偏好里找不到依据的问题，
   一律算大事，起气泡请示，不猜。
"""
from __future__ import annotations

import contextlib
import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from ..contract.broadcast import publish_tool_execution
from ..memory.commission_context import gather as gather_memory
from ..memory.commission_context import memory_refs, render_memory_block
from .adapters import (UnknownAdapterError, adapter_types, create_adapter_for,
                       BaseWorkerAdapter)
from .evaluator import Evaluator, load_roster
from .telemetry import normalize_signature

COST_SCORE = {"cheap": 1.0, "medium": 0.6, "high": 0.3}
MAX_OBJECTIVE = 400
MAX_DELIVERABLE = 400
MAX_CONSTRAINTS = 6
MAX_ACCEPTANCE_RULES = 6
MAX_ACCEPTANCE_RULE_CHARS = 400
MAX_FEEDBACK_CHARS = 600

# 只有活着的进程能推进的状态。它们同时是"该算进 Worker 负载"的那一批：
# blocked 在等老板、ready_to_report 在等验收，都不占人手；把停车场也算进
# 并发槽，攒几单就能让选将返回 no_worker_available。
IN_FLIGHT_STATUSES = ("briefing", "running", "auditing")


def in_flight_load(commissions: list[dict[str, Any]]) -> dict[str, int]:
    """按 Agent 数在手的督办，只数真的在跑的。"""
    load: dict[str, int] = {}
    for item in commissions:
        if item.get("agent_id") and item.get("status") in IN_FLIGHT_STATUSES:
            load[item["agent_id"]] = load.get(item["agent_id"], 0) + 1
    return load


# CommissionStore.transition 对 packet JSON 的硬限（server/interaction/threads.py）。
# 记忆正文顶格时整包会超限，那时丢 memory_block 保 memory_refs——派单不能因为
# 记忆太多而失败，稳定引用比正文重要。
MAX_PACKET_CHARS = 8000


@dataclass(frozen=True)
class BriefingPacket:
    """派单密件：Worker 唯一允许看到的工作单。不含思考过程，不含凭据。"""

    commission_id: str
    objective: str
    deliverable: str
    agent_id: str
    agent_name: str
    constraints: tuple[str, ...] = ()
    memory_refs: tuple[dict[str, Any], ...] = ()
    preferences: tuple[dict[str, Any], ...] = ()
    rubrics: tuple[dict[str, Any], ...] = ()
    acceptance: tuple[dict[str, Any], ...] = ()
    feedback: str = ""
    budget: dict[str, Any] = field(default_factory=dict)
    spin_guard: dict[str, Any] = field(default_factory=dict)
    memory_block: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)
    # 契约层（server/contract/）对这张单的判断结果。
    # 只读旁路：不拦派单，只把"这单离一份合格契约还差什么"记下来，
    # 让台账和世界都能看见。空 tuple = 全项通过（G1-G6 都没缺口）。
    contract_gaps: tuple[dict[str, Any], ...] = ()
    created_at: float = 0.0

    def as_dict(self, *, max_chars: int = 0) -> dict[str, Any]:
        """序列化成可持久化的密件。

        `max_chars` 是台账的落库上限；超限时丢掉 memory_block（保留 memory_refs），
        其余字段都有独立长度上限，丢掉正文后必定放得下。验收规则与上一轮批注也
        各有上限：单条超长或数量超编的规则会变成一条"无法评估"的规则，由初审
        判失败，而不是被悄悄删掉后放行。
        """
        data = {
            "commission_id": self.commission_id,
            "objective": self.objective,
            "deliverable": self.deliverable,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "constraints": list(self.constraints),
            "memory_refs": [dict(item) for item in self.memory_refs],
            "preferences": [dict(item) for item in self.preferences],
            "memory_block": self.memory_block,
            "rubrics": [dict(item) for item in self.rubrics],
            "acceptance": [dict(item) for item in self.acceptance],
            "feedback": _clip(self.feedback, MAX_FEEDBACK_CHARS),
            "budget": dict(self.budget),
            "spin_guard": dict(self.spin_guard),
            "telemetry": dict(self.telemetry),
            "contract_gaps": [dict(item) for item in self.contract_gaps],
            "created_at": self.created_at,
        }
        if max_chars and len(json.dumps(data, ensure_ascii=False)) > int(max_chars):
            data.pop("memory_block", None)
        return data

    def as_stored(self) -> dict[str, Any]:
        """落库/外发用的密件视图：与 CommissionStore 里那份逐字节一致。"""
        return self.as_dict(max_chars=MAX_PACKET_CHARS)


def _contract_gaps(commission: dict[str, Any], acceptance: Any) -> tuple[dict[str, Any], ...]:
    """用契约层（server/contract/）检查这一单，返回缺口清单。

    这是**只读旁路**：不拦派单、不改状态、不改任何既有行为。
    目的只有一个——把「这单离一份合格契约还差什么」记进密件，
    否则 server/contract/ 那 252 条测试覆盖的代码在运行时是死的。

    为什么不拦：派单路径上有 5 个调用方（app.py 的 REST、调度器、重试、
    超时重派、手工补派），拦一下就是行为变更，得先过用户。这里先做观察，
    等缺口数据攒出来再决定哪些该硬拦。

    任何异常都吞掉并返回空：契约层坏了不能把派单带崩。
    """
    try:
        from ..contract import Draft, Slot, collect_gaps

        title = str(commission.get("title") or "").strip()
        brief = str(commission.get("brief") or "").strip()
        # G1 要「动词 + 对象」且不超过 80 字；这里把标题和摘要合起来当目标。
        goal_text = (title or brief)[:200]
        draft = Draft(
            contract_id=str(commission.get("id") or ""),
            utterance=goal_text,
            goal=Slot(value=goal_text, source="user") if goal_text else None,
            acceptance=tuple(_acceptance_criteria(acceptance)),
        )
        gaps = collect_gaps(draft)
        return tuple(
            {"check": gap.check, "key": gap.key, "missing": list(gap.missing)}
            for gap in gaps
        )
    except Exception:
        return ()


def _acceptance_criteria(acceptance: Any) -> list[Any]:
    """把台账里的验收规则转成契约层的 Criterion，转不了的跳过。"""
    try:
        from ..contract import Criterion
    except Exception:
        return []
    out: list[Any] = []
    for item in (acceptance or ()):
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("criterion") or "").strip()
        if not text:
            continue
        try:
            out.append(Criterion(
                text=text[:400],
                required=bool(item.get("required", True)),
            ))
        except Exception:
            continue
    return out


def _clip(text: Any, limit: int) -> str:
    return text[:limit] if isinstance(text, str) else ""


def _json_safe(value: Any, depth: int = 0) -> Any:
    """把调用方给的验收规则收敛成可落库的 JSON 值，超长部分直接截断。"""
    if isinstance(value, str):
        return value[:200]
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if depth >= 3:
        return None
    if isinstance(value, dict):
        return {str(key)[:64]: _json_safe(item, depth + 1)
                for key, item in list(value.items())[:8]}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item, depth + 1) for item in list(value)[:8]]
    return None


def _clean_acceptance(acceptance: Any) -> tuple[dict[str, Any], ...]:
    """上限内原样收敛；超出上限的规则换成"无法评估"的 kind，初审会判失败。"""
    items = list(acceptance) if isinstance(acceptance, (list, tuple)) else []
    rules: list[dict[str, Any]] = []
    for item in items[:MAX_ACCEPTANCE_RULES]:
        clean = _json_safe(item) if isinstance(item, dict) and item else {"kind": "invalid"}
        if len(json.dumps(clean, ensure_ascii=False)) > MAX_ACCEPTANCE_RULE_CHARS:
            clean = {"kind": "acceptance_rule_too_large"}
        rules.append(clean)
    if len(items) > MAX_ACCEPTANCE_RULES:
        rules.append({"kind": "acceptance_rule_overflow"})
    return tuple(rules)


def _deliverable_for(agent: dict[str, Any]) -> str:
    summary = _clip(agent.get("summary"), 200)
    return f"按本行职责交付：{summary}" if summary else "按密件要求交付"


def roster_agents(roster: dict[str, Any]) -> list[dict[str, Any]]:
    """花名册条目列表。v2 起叫 `agents:`，兼容 v1 的 `workers:`。"""
    for key in ("agents", "workers"):
        entries = roster.get(key)
        if isinstance(entries, list) and entries:
            return entries
    return []


def select_agent(roster: dict[str, Any], text: str,
                 load: dict[str, int] | None = None,
                 force_id: str | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    """Score every roster worker; return (best, ranked). Best is None when all are full."""
    load = load or {}
    selection = roster.get("selection") or {}
    strength_weight = float(selection.get("strength_weight", 1.0))
    load_weight = float(selection.get("load_weight", 0.6))
    cost_weight = float(selection.get("cost_weight", 0.4))
    default_cap = int(selection.get("max_concurrent_default", 2) or 2)
    ranked: list[dict[str, Any]] = []
    entries = roster_agents(roster)
    for worker in entries:
        if not isinstance(worker, dict) or not worker.get("id"):
            continue
        # Explicit opt-out for workers that need a human decision before they may
        # touch production files (currently Codex's Windows sandbox). Missing
        # `enabled` keeps the old behavior: every roster row is selectable.
        if worker.get("enabled") is False:
            continue
        strengths = [str(item) for item in (worker.get("strengths") or [])]
        hits = [item for item in strengths if item and item in text]
        cost = COST_SCORE.get(str(worker.get("cost_tier")), 0.5)
        concurrent = int(load.get(worker["id"], 0) or 0)
        cap = int(worker.get("max_concurrent") or default_cap)
        score = round(len(hits) * strength_weight + cost * cost_weight - concurrent * load_weight, 3)
        ranked.append({"agent_id": worker["id"], "name": worker.get("name") or worker["id"],
                       "score": score, "hits": hits, "concurrent": concurrent, "cap": cap,
                       "available": concurrent < cap})
    ranked.sort(key=lambda item: item["score"], reverse=True)
    if force_id is not None:
        # A lease may pin the worker (C2 only opens scribe). Pinning still
        # respects enabled and capacity; it never bypasses the roster.
        row = next((item for item in ranked if item["agent_id"] == force_id), None)
        if row is None or not row["available"]:
            return None, ranked
        for worker in entries:
            if isinstance(worker, dict) and worker.get("id") == force_id:
                return worker, ranked
        return None, ranked
    available = [item for item in ranked if item["available"]]
    if not available:
        return None, ranked
    winner = available[0]
    for worker in entries:
        if isinstance(worker, dict) and worker.get("id") == winner["agent_id"]:
            return worker, ranked
    return None, ranked


class Dispatcher:
    """唯一写 Commission 状态机的地方，也是唯一决定"这个活交给谁干"的地方。

    关于可插拔：本类从不判断 Agent 是什么类型。它只做两件事——按花名册的
    `adapter:` 字符串经注册表拿一个适配器实例，然后驱动那三个标准异步方法。
    新增一种干活方式（新的 CLI、新的 MCP Server、新的远端服务）只需要在
    `server/assistant/adapters/` 下加一个模块并 @register_adapter，这里零改动。
    """

    def __init__(self, *, commissions, roster: dict[str, Any] | None = None,
                 evaluator: Evaluator | None = None, memory_provider: Callable[[str], Any] | None = None,
                 clock: Callable[[], float] = time.time, telemetry: Any = None,
                 adapter_factory: Callable[[dict[str, Any]], BaseWorkerAdapter] | None = None):
        self.commissions = commissions
        self.roster = roster if roster is not None else load_roster()
        self.evaluator = evaluator if evaluator is not None else Evaluator(roster=self.roster)
        self.memory_provider = memory_provider
        self.clock = clock
        self.telemetry = telemetry
        # 可注入的适配器工厂：测试里换成一个假工厂即可验证派发全程，
        # 生产环境用注册表默认实现。
        self.adapter_factory = adapter_factory or create_adapter_for
        # 执行权租约：检查与占位之间没有 await，在单事件循环里是原子的；加锁是
        # 为了万一有线程池调用方也安全。重复领取直接失败而不是排队——排队只会
        # 让第二次点击白等一圈，然后跑一份重复的工。
        self._claim_lock = threading.Lock()
        self._active_runs: set[str] = set()

    # ---------- 适配器 ----------
    def agent_entry(self, agent_id: str) -> dict[str, Any]:
        """花名册里对应 id 的整条配置（含 adapter / executable / endpoint）。"""
        for entry in roster_agents(self.roster):
            if isinstance(entry, dict) and str(entry.get("id")) == str(agent_id):
                return entry
        raise KeyError(agent_id)

    def adapter_for(self, agent_id: str) -> BaseWorkerAdapter:
        """经注册表取适配器。花名册写了没登记的类型时抛 UnknownAdapterError，
        由调用方决定换将还是请示老板——注册表自己不猜。"""
        return self.adapter_factory(self.agent_entry(agent_id))

    def available_adapter_types(self) -> list[str]:
        return adapter_types()

    # ---------- 阶段推进 ----------
    def open(self, title: str, brief: str = "", thread_id: str = "", *,
             request_id: str | None = None) -> dict[str, Any]:
        return self.commissions.create(title, brief, thread_id, request_id=request_id)

    def dispatch(self, commission_id: str, *, intent: str = "",
                 acceptance: Any = None, agent_id: str | None = None) -> tuple[dict[str, Any], BriefingPacket]:
        """DRAFTING -> BRIEFING: pack memory, pick a worker, store the packet."""
        commission = self.commissions.get(commission_id)
        if commission["status"] != "drafting":
            raise ValueError("commission_not_drafting")
        text = f"{commission['title']} {commission['brief']} {intent}".strip()
        load = in_flight_load(self.commissions.open_commissions())
        agent, ranked = select_agent(self.roster, text, load, force_id=agent_id)
        if agent is None:
            raise LookupError("no_worker_available")
        memory = self._memory(text)
        rubrics = tuple({"id": item["id"], "severity": item["severity"], "layer": item["layer"]}
                        for item in self.evaluator.rubrics.list(active_only=True))
        dispatch_cfg = self.roster.get("dispatch") or {}
        packet = BriefingPacket(
            commission_id=commission_id,
            objective=_clip(commission["brief"] or commission["title"], MAX_OBJECTIVE),
            deliverable=_deliverable_for(agent),
            agent_id=str(agent["id"]),
            agent_name=str(agent.get("name") or agent["id"]),
            constraints=tuple(_clip(item, 120) for item in (agent.get("weaknesses") or []))[:MAX_CONSTRAINTS],
            memory_refs=tuple(memory_refs(memory)),
            preferences=tuple({"dimension": ref.get("dimension"), "value": ref.get("value")}
                              for ref in memory_refs(memory) if ref.get("type") == "preference"),
            rubrics=rubrics,
            acceptance=_clean_acceptance(acceptance),
            contract_gaps=_contract_gaps(commission, acceptance),
            budget={"max_steps": int(dispatch_cfg.get("max_steps", 4) or 4),
                    # 单次调用是质量闸；整单累计只做成本告警，不能拿重试次数当上下文膨胀。
                    "max_prompt_tokens_per_call": int(
                        dispatch_cfg.get("max_prompt_tokens_per_call")
                        or dispatch_cfg.get("max_prompt_tokens") or 12000),
                    "max_total_prompt_tokens": int(
                        dispatch_cfg.get("max_total_prompt_tokens") or 0),
                    "max_retry": self.evaluator.MAX_RETRY,
                    "eval_commands": [str(item) for item in (agent.get("eval_commands") or [])]},
            spin_guard=dict((dispatch_cfg.get("spin_guard") or {"max_identical": 3})),
            memory_block=render_memory_block(memory),
            telemetry={"proxy_url": str(dispatch_cfg.get("telemetry_proxy_url") or ""),
                       "commission_id": commission_id, "agent_id": str(agent["id"])},
            created_at=self.clock(),
        )
        updated = self.commissions.transition(commission_id, "briefing", agent_id=packet.agent_id,
                                             packet=packet.as_stored())
        return updated, packet

    def retry_with_feedback(self, commission_id: str, feedback: str) -> dict[str, Any]:
        """RUNNING 复查：把上一轮批注写回密件，让执行体带着批注重做。

        状态机没有 running->running 这条边，也不该为改写密件新开一条；这里走
        running->auditing->running 两条既有边。中间态不写 audit，上一轮的失败
        结论原样保留；回到 running 时把 feedback 一起落进 packet。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] != "running":
            raise ValueError("commission_not_running")
        packet = dict(commission.get("packet") or {})
        packet["feedback"] = _clip(feedback, MAX_FEEDBACK_CHARS)
        self.commissions.transition(commission_id, "auditing")
        return self.commissions.transition(commission_id, "running", packet=packet)

    def start(self, commission_id: str) -> dict[str, Any]:
        return self.commissions.transition(commission_id, "running")

    # ---------- 驱动执行体 ----------
    async def run_commission(self, commission_id: str) -> dict[str, Any]:
        """RUNNING 期间驱动适配器：prepare_context -> execute 事件流 -> submit。

        事件流的处置规则：
          progress -> 记步，并喂给遥测里的熔断器（同一步骤签名连续重复即熔断）
          token    -> 落一条遥测 span（Dual-Shim 那条链路）
          artifact -> 收下；其后若又出现 error，这一版不算交差
          error    -> 记下并中止

        适配器起不来、抛异常、或鬼打墙熔断时，先 cancel() 回收进程，再转 BLOCKED
        请示老板。绝不假装执行过，也绝不无限重试。

        执行权：进来先领租约。重复点击、重试、并发 /run 只会有一个真正起身，
        第二个拿到 ValueError——只查 status 防不住并发，检查和起适配器之间
        隔着好几轮 await，两个请求都能看到"还是 running"。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] != "running":
            raise ValueError("commission_not_running")
        if not self._claim_run(commission_id):
            raise ValueError("commission_already_running")
        # 每次开跑重新开窗：否则上一轮熔断留下的签名，会让这一轮的第一步就跳闸。
        if self.telemetry is not None:
            with contextlib.suppress(Exception):
                self.telemetry.guard.reset(commission_id)
        try:
            return await self._run_adapter(commission_id)
        finally:
            self._release_run(commission_id)

    async def _run_adapter(self, commission_id: str) -> dict[str, Any]:
        commission = self.commissions.get(commission_id)
        if commission["status"] != "running":
            raise ValueError("commission_not_running")
        packet = commission.get("packet") or {}
        agent_id = str(commission.get("agent_id") or "")
        try:
            adapter = self.adapter_for(agent_id)
        except UnknownAdapterError as exc:
            return {"commission": self.block(
                commission_id, f"花名册上的 {agent_id or '这个 Agent'} 没有可用执行体：{exc}。换将，还是这单算了？"),
                "verdict": None, "failure": "adapter_unavailable"}
        max_identical = int((packet.get("spin_guard") or {}).get("max_identical") or 3)
        signatures: list[str] = []
        artifact: dict[str, Any] | None = None
        failure: str | None = None
        failure_kind: str | None = None
        # 这是第几次做这一单。适配器要用它区分"重投同一次"与"初审打回后重做"：
        # durable 的幂等键如果只有 commission_id，重做会拿回上一版已终态的任务，
        # 执行体根本不会被再拉起，旧成果被原样再交一次。只传给适配器，不写进
        # 台账里的密件（协议 §19 的形状不变）。
        attempt = int(commission.get("retry_count") or 0)
        worker_packet = {**packet, "attempt": attempt}
        try:
            context = await adapter.prepare_context(commission_id, worker_packet)
        except Exception as exc:
            return {"commission": self.block(
                commission_id, f"执行体没起来：{type(exc).__name__}。要我再试一次吗？"),
                "verdict": None, "failure": f"prepare_failed:{type(exc).__name__}"}
        try:
            async for event in adapter.execute({"context": context, "commission_id": commission_id,
                                                "packet": worker_packet}):
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("type") or "")
                step_idx = int(event.get("step_idx") or 0)
                if kind == "progress":
                    self.commissions.record_step(commission_id, step_idx or 1)
                    # §25.2 TOOL_EXECUTION（至多一次）：一条进展就是一次真实工具
                    # 执行的可观测信号。tool 如实写执行者（agent_id），适配器事件
                    # 没带的 steps_total / 状态名不编造。发布失败不冒泡（旁路）。
                    publish_tool_execution(
                        task_id=commission_id, tool=str(agent_id), status="running",
                        steps_done=step_idx or 1,
                        detail=str(event.get("text") or event.get("detail") or ""))
                    signature = normalize_signature(event)
                    tripped = self._spinning(signatures, commission_id, signature,
                                             agent_id, max_identical)
                    if tripped:
                        await adapter.cancel()
                        return {"commission": self.block(
                            commission_id, "它开始在原地打转了，我先把闸拉了。换将，还是这单算了？"),
                            "verdict": None, "failure": "spinning"}
                elif kind == "token":
                    self._record_token(commission_id, agent_id, event)
                elif kind == "artifact":
                    artifact = event.get("artifact")
                elif kind == "error":
                    failure = str(event.get("text") or "执行体报错")[:240]
                    candidate = event.get("blocked_kind")
                    failure_kind = candidate if candidate in ("clarification", "approval") else None
                    break
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"[:240]
        finally:
            with contextlib.suppress(Exception):
                await adapter.cancel()
        if artifact is None:
            reason = failure or "它跑完了但没交差"
            return {"commission": self.block(
                commission_id, f"{reason}。要我再催一次，还是这单算了？",
                kind=failure_kind or "clarification"),
                "verdict": None, "failure": failure or "no_artifact"}
        if failure:
            # artifact 之后又报错：这一版没有收尾。"交了又炸了"和"交差了"不是
            # 一回事，部分产物留给老板定夺，不当成功交付。
            return {"commission": self.block(
                commission_id, f"它交了差又报错：{failure}。部分产物还在，要我核一遍再交吗？"),
                "verdict": None, "failure": f"partial_artifact:{failure}"}
        usage, spinning = self.audit_inputs(commission_id)
        commission, verdict = self.submit(commission_id, artifact, usage=usage, spinning=spinning)
        return {"commission": commission, "verdict": verdict, "failure": failure}

    def _spinning(self, signatures: list[str], commission_id: str, signature: str,
                  agent_id: str, max_identical: int) -> bool:
        """鬼打墙判据只有一个来源：遥测里的 SpinningGuard。

        每一步签名都喂给它，而不是等本地计数跳闸了才补喂一次——后者让告警永远
        进不了 spinning_alerts，抽屉里看得见熔断、战况里查无此事。遥测缺失时才
        回落到本地计数，行为与从前一致。
        """
        if self.telemetry is not None:
            try:
                return bool(self.telemetry.guard.observe(
                    commission_id, signature, agent_id=agent_id,
                    max_identical=max_identical, channel="progress")["tripped"])
            except Exception:
                pass
        signatures.append(signature)
        return (len(signatures) >= max_identical
                and len(set(signatures[-max_identical:])) == 1)

    def audit_inputs(self, commission_id: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
        """初审的真实读数：这一单的实际用量 + 熔断告警。

        `run_commission` 和外部 `/submit` 走同一个入口，两条路径判据因此一致，
        不会出现"内部链路不查预算、外部链路查"的双标。遥测缺失时两个都是 None，
        初审按"没有真实 span"处理，不猜。
        """
        if self.telemetry is None:
            return None, None
        usage: dict[str, Any] | None = None
        spinning: dict[str, Any] | None = None
        try:
            usage = self.telemetry.store.commission_usage(commission_id)
        except Exception:
            usage = None
        try:
            alerts = [item for item in self.telemetry.guard.alerts()
                      if item["commission_id"] == commission_id]
            if alerts:
                spinning = {"tripped": True, "repeats": alerts[-1]["repeats"]}
        except Exception:
            spinning = None
        return usage, spinning

    def _claim_run(self, commission_id: str) -> bool:
        with self._claim_lock:
            if commission_id in self._active_runs:
                return False
            self._active_runs.add(commission_id)
            return True

    def _release_run(self, commission_id: str) -> None:
        with self._claim_lock:
            self._active_runs.discard(commission_id)

    def _record_token(self, commission_id: str, agent_id: str, event: dict[str, Any]) -> None:
        """把一次模型调用落成 span。遥测记不上只当这次不计入战况，不带崩执行。"""
        if self.telemetry is None:
            return
        try:
            self.telemetry.record_event({
                "commission_id": commission_id, "agent_id": agent_id,
                "step_idx": int(event.get("step_idx") or 0),
                "model": str(event.get("model") or ""),
                "prompt_tokens": int(event.get("prompt_tokens") or 0),
                "completion_tokens": int(event.get("completion_tokens") or 0),
                "ttft_ms": int(event.get("ttft_ms") or 0),
                "duration_ms": int(event.get("duration_ms") or 0),
                "status_code": int(event.get("status_code") or 0),
                "error": str(event.get("error") or ""),
            })
        except Exception:
            return

    def block(self, commission_id: str, question: str,
              kind: str = "clarification") -> dict[str, Any]:
        """RUNNING/BRIEFING -> BLOCKED. 琐事代答，大事请示，由 trivia_answer 决定。

        `kind` is structured, not inferred from the question text: a clarification
        may be answered by the C2 orchestrator; an approval must go through the
        confirmation card and CAS.
        """
        if kind not in ("clarification", "approval"):
            kind = "clarification"
        return self.commissions.transition(commission_id, "blocked",
                                           blocked_question=question, blocked_kind=kind)

    def trivia_answer(self, question: str) -> str | None:
        """An answer grounded in the roster's trivia policy, or None when there is none."""
        if not isinstance(question, str) or not question.strip():
            return None
        for policy in self.roster.get("trivia_policy") or []:
            if not isinstance(policy, dict):
                continue
            pattern = policy.get("pattern")
            answer = policy.get("answer")
            if not pattern or not isinstance(answer, str) or not answer.strip():
                continue
            try:
                if re.search(pattern, question):
                    return answer.strip()
            except re.error:
                continue
        return None

    def answer(self, commission_id: str, answer: str) -> tuple[dict[str, Any], str, bool]:
        """BLOCKED -> RUNNING. Returns (commission, reply, auto_answered).

        `retry_delta=1` 是给 durable 执行体用的：它按 attempt 幂等（见
        `_run_adapter`），attempt 不变就会拿回内核里同一个已终态的任务，
        执行体不再被拉起。请示获答是一次新的开工，不是重投，所以计数要动。
        对 manual / cli_subproc 没有影响——它们的 attempt 只用于遥测与打回。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] != "blocked":
            raise ValueError("commission_not_blocked")
        reply, auto = answer, False
        if not (isinstance(answer, str) and answer.strip()):
            reply = self.trivia_answer(commission["blocked_question"])
            if reply is None:
                raise ValueError("blocked_question_needs_an_answer")
            auto = True
        updated = self.commissions.transition(commission_id, "running", blocked_question="",
                                              retry_delta=1)
        return updated, reply, auto

    # ---------- 交差与初审 ----------
    def submit(self, commission_id: str, artifact: dict[str, Any], *, usage: dict[str, Any] | None = None,
               spinning: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        """RUNNING -> AUDITING -> (RUNNING | READY_TO_REPORT | BLOCKED)."""
        commission = self.commissions.get(commission_id)
        if commission["status"] != "running":
            raise ValueError("commission_not_running")
        packet = commission.get("packet") or {}
        verdict = self.evaluator.review(commission=commission, artifact=artifact, packet=packet,
                                        usage=usage, spinning=spinning)
        self.commissions.transition(commission_id, "auditing", audit=verdict)
        if verdict["passed"]:
            delivery = {"summary": _clip(artifact.get("summary"), 400),
                        "intercepted": _clip(artifact.get("intercepted"), 400),
                        "artifacts": list(artifact.get("artifacts") or [])[:12]}
            return self.commissions.transition(commission_id, "ready_to_report", delivery=delivery), verdict
        if int(commission["retry_count"]) + 1 > self.evaluator.MAX_RETRY:
            # 重试耗尽：不再往 Worker 身上压老板的时间，带完整批注请示。
            return self.commissions.transition(commission_id, "blocked",
                                               blocked_question=_clip(verdict["scolding"], 400),
                                               retry_delta=1), verdict
        return self.commissions.transition(commission_id, "running", retry_delta=1), verdict

    def accept(self, commission_id: str) -> dict[str, Any]:
        """READY_TO_REPORT -> DONE: 归档并触发一次 Eval 自进化检查。"""
        commission = self.commissions.get(commission_id)
        if commission["status"] != "ready_to_report":
            raise ValueError("commission_not_ready_to_report")
        return self.commissions.transition(commission_id, "done")

    def reassign(self, commission_id: str) -> dict[str, Any]:
        """换将重派（协议 §23.3 dispatch:reassign）：BLOCKED -> RUNNING 并重新选将。

        只对 blocked 单生效：running 的单没有合法迁移边，auditing 的单正在初审，
        都不该被一张卡片上的按钮掀翻。排除现任将的办法是把它在负载表里顶满
        （不改 select_agent 的签名）；花名册只剩一个人时承认换不动，原样解冻
        重跑——由上层照实说，不编造"已换将"。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] != "blocked":
            raise ValueError("commission_not_blocked")
        text = f"{commission['title']} {commission['brief']}".strip()
        load = in_flight_load(self.commissions.open_commissions())
        current = str(commission.get("agent_id") or "")
        if current:
            load[current] = max(load.get(current, 0), 10 ** 6)
        agent, _ranked = select_agent(self.roster, text, load)
        if agent is not None:
            return self.commissions.transition(commission_id, "running",
                                               agent_id=str(agent["id"]),
                                               blocked_question="")
        return self.commissions.transition(commission_id, "running", blocked_question="")

    def reject(self, commission_id: str, boss_feedback: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
        """READY_TO_REPORT -> RUNNING, and learn one new rubric from the Delta.

        漏审点捕获走 loop.capture_eval_delta（V3 §2.4）：初审凭什么放行、老板
        挑了什么地方，两头都写进 Delta，再炼成一条带证据的新判例。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] != "ready_to_report":
            raise ValueError("commission_not_ready_to_report")
        audit = dict(commission.get("audit") or {})
        # 老板打回次数单独计：初审打回有 MAX_RETRY 兜底，老板的打回是特权，
        # 但次数要留痕——第三次前台会换一套说法（委屈吐槽防线）。
        audit["boss_reject_count"] = int(audit.get("boss_reject_count") or 0) + 1
        from .loop import capture_eval_delta
        delta = capture_eval_delta(commission=commission, boss_feedback=boss_feedback,
                                   evaluator=self.evaluator)
        rubric_ids = list(commission.get("rubric_ids") or [])
        if delta is not None and delta.learned_rubric_id:
            rubric_ids.append(delta.learned_rubric_id)
        updated = self.commissions.transition(commission_id, "running", rubric_ids=rubric_ids[-12:],
                                              audit={**audit,
                                                     "boss_feedback": _clip(boss_feedback, 400),
                                                     "learned_rubric": delta.learned_rubric_id if delta else None,
                                                     "delta": delta.as_dict() if delta else None})
        return updated, (delta.as_dict() if delta is not None else None), audit["boss_reject_count"]

    def cancel(self, commission_id: str, reason: str = 'cancelled') -> dict[str, Any]:
        """取消一单：迁到 done，并在 audit 里留一条取消记录。

        为什么不能只做 transition(id, 'done')：台账只有 done 一个终态，
        「老板取消」和「验收通过」在状态列上完全同形。像素世界的投影
        原先就把取消当成交付（还加信仰点），根因就在这儿——世界读不到
        「这单是被撤掉的」这个事实。

        修法不新增协议字段（那要 [PROTOCOL_REQUEST]），用已有的 audit JSON：
        取消时写入 {"cancelled": True, "cancel_reason": ..., "cancelled_at": ...}。
        读取方（投影 / 日报）看 audit.cancelled 就能区分。

        reason 允许调用方给具体原因；默认 'cancelled'。
        """
        commission = self.commissions.get(commission_id)
        if commission["status"] == "done":
            return commission
        audit = dict(commission.get("audit") or {})
        if audit.get("cancelled"):
            return commission  # 已取消过，幂等
        audit.update({
            "cancelled": True,
            "cancel_reason": _clip(str(reason or 'cancelled'), 120),
            "cancelled_at": time.time(),
        })
        return self.commissions.transition(commission_id, "done", audit=audit)

    # ---------- 内部 ----------
    def _memory(self, text: str) -> dict[str, Any]:
        if self.memory_provider is None:
            return gather_memory()
        try:
            result = self.memory_provider(text)
        except Exception:
            return gather_memory()
        return result if isinstance(result, dict) else gather_memory()


__all__ = ["BriefingPacket", "Dispatcher", "select_agent"]
