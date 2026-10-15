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
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

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
    budget: dict[str, Any] = field(default_factory=dict)
    spin_guard: dict[str, Any] = field(default_factory=dict)
    memory_block: str = ""
    telemetry: dict[str, Any] = field(default_factory=dict)
    created_at: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "commission_id": self.commission_id,
            "objective": self.objective,
            "deliverable": self.deliverable,
            "agent_id": self.agent_id,
            "agent_name": self.agent_name,
            "constraints": list(self.constraints),
            "memory_refs": [dict(item) for item in self.memory_refs],
            "preferences": [dict(item) for item in self.preferences],
            "rubrics": [dict(item) for item in self.rubrics],
            "budget": dict(self.budget),
            "spin_guard": dict(self.spin_guard),
            "telemetry": dict(self.telemetry),
            "created_at": self.created_at,
        }


def _clip(text: Any, limit: int) -> str:
    return text[:limit] if isinstance(text, str) else ""


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
                 load: dict[str, int] | None = None) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
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
    def open(self, title: str, brief: str = "", thread_id: str = "") -> dict[str, Any]:
        return self.commissions.create(title, brief, thread_id)

    def dispatch(self, commission_id: str, *, intent: str = "") -> tuple[dict[str, Any], BriefingPacket]:
        """DRAFTING -> BRIEFING: pack memory, pick a worker, store the packet."""
        commission = self.commissions.get(commission_id)
        if commission["status"] != "drafting":
            raise ValueError("commission_not_drafting")
        text = f"{commission['title']} {commission['brief']} {intent}".strip()
        load: dict[str, int] = {}
        for item in self.commissions.open_commissions():
            if item["agent_id"]:
                load[item["agent_id"]] = load.get(item["agent_id"], 0) + 1
        agent, ranked = select_agent(self.roster, text, load)
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
            budget={"max_steps": int(dispatch_cfg.get("max_steps", 4) or 4),
                    "max_prompt_tokens": int(dispatch_cfg.get("max_prompt_tokens", 12000) or 12000),
                    "max_retry": self.evaluator.MAX_RETRY,
                    "eval_commands": [str(item) for item in (agent.get("eval_commands") or [])]},
            spin_guard=dict((dispatch_cfg.get("spin_guard") or {"max_identical": 3})),
            memory_block=render_memory_block(memory),
            telemetry={"proxy_url": str(dispatch_cfg.get("telemetry_proxy_url") or ""),
                       "commission_id": commission_id, "agent_id": str(agent["id"])},
            created_at=self.clock(),
        )
        updated = self.commissions.transition(commission_id, "briefing", agent_id=packet.agent_id,
                                             packet=packet.as_dict())
        return updated, packet

    def start(self, commission_id: str) -> dict[str, Any]:
        return self.commissions.transition(commission_id, "running")

    # ---------- 驱动执行体 ----------
    async def run_commission(self, commission_id: str) -> dict[str, Any]:
        """RUNNING 期间驱动适配器：prepare_context -> execute 事件流 -> submit。

        事件流的处置规则：
          progress -> 记步，并喂给鬼打墙检测（同一步骤签名连续重复即熔断）
          token    -> 落一条遥测 span（Dual-Shim 那条链路）
          artifact -> 收下，流结束后交初审室
          error    -> 记下并中止

        适配器起不来、抛异常、或鬼打墙熔断时，先 cancel() 回收进程，再转 BLOCKED
        请示老板。绝不假装执行过，也绝不无限重试。
        """
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
        try:
            context = await adapter.prepare_context(commission_id, packet)
        except Exception as exc:
            return {"commission": self.block(
                commission_id, f"执行体没起来：{type(exc).__name__}。要我再试一次吗？"),
                "verdict": None, "failure": f"prepare_failed:{type(exc).__name__}"}
        try:
            async for event in adapter.execute({"context": context, "commission_id": commission_id,
                                                "packet": packet}):
                if not isinstance(event, dict):
                    continue
                kind = str(event.get("type") or "")
                step_idx = int(event.get("step_idx") or 0)
                if kind == "progress":
                    self.commissions.record_step(commission_id, step_idx or 1)
                    signature = normalize_signature(event)
                    signatures.append(signature)
                    tripped = (len(signatures) >= max_identical
                               and len(set(signatures[-max_identical:])) == 1)
                    if tripped and self.telemetry is not None:
                        with contextlib.suppress(Exception):
                            self.telemetry.guard.observe(commission_id, signature, agent_id=agent_id)
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
                    break
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"[:240]
        finally:
            with contextlib.suppress(Exception):
                await adapter.cancel()
        if artifact is None:
            reason = failure or "它跑完了但没交差"
            return {"commission": self.block(
                commission_id, f"{reason}。要我再催一次，还是这单算了？"),
                "verdict": None, "failure": failure or "no_artifact"}
        commission, verdict = self.submit(commission_id, artifact)
        return {"commission": commission, "verdict": verdict, "failure": failure}

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

    def block(self, commission_id: str, question: str) -> dict[str, Any]:
        """RUNNING/BRIEFING -> BLOCKED. 琐事代答，大事请示，由 trivia_answer 决定。"""
        return self.commissions.transition(commission_id, "blocked", blocked_question=question)

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
        """BLOCKED -> RUNNING. Returns (commission, reply, auto_answered)."""
        commission = self.commissions.get(commission_id)
        if commission["status"] != "blocked":
            raise ValueError("commission_not_blocked")
        reply, auto = answer, False
        if not (isinstance(answer, str) and answer.strip()):
            reply = self.trivia_answer(commission["blocked_question"])
            if reply is None:
                raise ValueError("blocked_question_needs_an_answer")
            auto = True
        updated = self.commissions.transition(commission_id, "running", blocked_question="")
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

    def cancel(self, commission_id: str) -> dict[str, Any]:
        commission = self.commissions.get(commission_id)
        if commission["status"] == "done":
            return commission
        return self.commissions.transition(commission_id, "done")

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
