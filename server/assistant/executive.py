"""内阁总管门面：把慢环调度、观测网关、自进化初审和督办台账接在一起。

这一层是唯一同时握着四件东西的地方，因此它也只做"接线 + 读数"：
台前怎么说由 orchestrator 决定，抽屉怎么画由 web 决定，这里只给数据。
任何读失败都降级为空列表/空统计，绝不让抽屉把主页面带崩。
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from ..interaction.threads import CommissionStore
from .dispatcher import Dispatcher
from .evaluator import Evaluator, load_roster
from .telemetry import SpinningGuard, TelemetryGateway, TelemetryStore

DEFAULT_TELEMETRY_WINDOW = 24 * 60 * 60


class ExecutiveService:
    def __init__(self, directory, *, llm: Any = None, notes: Any = None, threads: Any = None,
                 preference_store: Any = None, roster: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.time, commissions_path: str | None = None):
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        self.clock = clock
        self.llm = llm
        self.ledger = CommissionStore(commissions_path or str(directory / "commissions.db"))
        self.telemetry = TelemetryGateway(TelemetryStore(directory / "telemetry.db", clock=clock),
                                          guard=SpinningGuard(), clock=clock)
        self.evaluator = Evaluator(roster=roster or load_roster(),
                                   path=directory / "executive.db", clock=clock)
        self.evaluator.seed()
        self.dispatcher = Dispatcher(commissions=self.ledger, evaluator=self.evaluator,
                                     roster=self.evaluator.roster,
                                     memory_provider=self._memory_provider(notes, threads, preference_store),
                                     clock=clock)

    # ---------- 记忆注入 ----------
    @staticmethod
    def _memory_provider(notes, threads, preference_store):
        from ..memory.commission_context import gather

        def notes_reader(query):
            return (notes.search(query) if notes is not None and hasattr(notes, "search") else []) or []

        def threads_reader(query):
            if threads is None or not hasattr(threads, "list_threads"):
                return []
            return [{"id": item.get("id"), "title": item.get("title"), "status": item.get("status")}
                    for item in (threads.list_threads() or [])][:8]

        def preference_reader(query):
            if preference_store is None or not hasattr(preference_store, "list_claims"):
                return []
            return [{"dimension": item.get("dimension"), "value": item.get("value")}
                    for item in (preference_store.list_claims() or [])][:8]

        return lambda query: gather(notes_provider=notes_reader, threads_provider=threads_reader,
                                    preference_provider=preference_reader, query=query)

    # ---------- 慢环入口 ----------
    def open(self, title: str, brief: str = "", thread_id: str = "") -> dict[str, Any]:
        return self.dispatcher.open(title, brief, thread_id)

    def dispatch(self, commission_id: str, *, intent: str = "") -> dict[str, Any]:
        commission, packet = self.dispatcher.dispatch(commission_id, intent=intent)
        return {"commission": commission, "packet": packet.as_dict()}

    def start(self, commission_id: str) -> dict[str, Any]:
        return self.dispatcher.start(commission_id)

    def delegate(self, text: str, *, thread_id: str = "") -> dict[str, Any]:
        """后台接单：open -> dispatch -> start。任何一步失败都留痕，不向外抛。"""
        title = (text or "").strip()[:80] or "未命名督办"
        commission = self.dispatcher.open(title, text or "", thread_id)
        try:
            self.dispatcher.dispatch(commission["id"], intent=text or "")
            commission = self.dispatcher.start(commission["id"])
        except LookupError:
            # 选不出将：先承认进了组密件阶段，再带一句请示转 BLOCKED。
            # 只用协议 §19 里写明的迁移，不为了顺手新开一条边。
            self.ledger.transition(commission["id"], "briefing")
            commission = self.dispatcher.block(commission["id"], "这单没人能接：花名册上的 Worker 都满载了。换将，还是等一轮？")
        except Exception as exc:  # A broken dispatch must never break the voice turn.
            self.ledger.transition(commission["id"], "blocked",
                                        blocked_question=f"派单没组成：{type(exc).__name__}。要我再试一次吗？")
            commission = self.ledger.get(commission["id"])
        return {"commission_id": commission["id"], "status": commission["status"],
                "agent_id": commission["agent_id"], "agent_name": self._agent_name(commission["agent_id"])}

    def _agent_name(self, agent_id: str) -> str:
        for worker in self.evaluator.roster.get("workers") or []:
            if isinstance(worker, dict) and worker.get("id") == agent_id:
                return str(worker.get("name") or agent_id)
        return ""

    # ---------- 台账读数 ----------
    def commission(self, commission_id: str) -> dict[str, Any]:
        return self.ledger.get(commission_id)

    def commissions(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.ledger.list_commissions(status, limit)

    def commission_events(self, commission_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.ledger.events(commission_id, limit)

    def open_commissions(self) -> list[dict[str, Any]]:
        return self.ledger.open_commissions()

    def pending_reports(self) -> list[dict[str, Any]]:
        """READY_TO_REPORT 里还没被老板看过的那几单——心流防打扰只靠这份清单。"""
        return self.ledger.list_commissions("ready_to_report", 20)

    # ---------- 老板动作 ----------
    def answer(self, commission_id: str, answer: str = "") -> dict[str, Any]:
        commission, reply, auto = self.dispatcher.answer(commission_id, answer)
        return {"commission": commission, "reply": reply, "auto_answered": auto}

    def submit(self, commission_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        usage = self.telemetry.store.commission_usage(commission_id)
        alerts = [item for item in self.telemetry.guard.alerts() if item["commission_id"] == commission_id]
        spinning = {"tripped": bool(alerts), "repeats": alerts[-1]["repeats"]} if alerts else None
        commission, verdict = self.dispatcher.submit(commission_id, artifact, usage=usage, spinning=spinning)
        return {"commission": commission, "verdict": verdict}

    def accept(self, commission_id: str) -> dict[str, Any]:
        return self.dispatcher.accept(commission_id)

    def reject(self, commission_id: str, boss_feedback: str) -> dict[str, Any]:
        commission, rubric = self.dispatcher.reject(commission_id, boss_feedback)
        return {"commission": commission, "rubric": rubric}

    def cancel(self, commission_id: str) -> dict[str, Any]:
        return self.dispatcher.cancel(commission_id)

    # ---------- 观测 ----------
    def telemetry_summary(self, *, window_seconds: int = DEFAULT_TELEMETRY_WINDOW,
                          now: float | None = None) -> dict[str, Any]:
        now = self.clock() if now is None else float(now)
        return self.telemetry.summary(since=now - float(window_seconds))

    def commission_usage(self, commission_id: str) -> dict[str, Any]:
        return self.telemetry.store.commission_usage(commission_id)

    def telemetry_report(self, *, window_seconds: int = DEFAULT_TELEMETRY_WINDOW) -> str:
        """小溪微毒舌口吻的瓶颈分析（Role 2 的口吻、Role 1 的数字）。

        没有真实 span 时返回 ""——抽屉该显示"今天还没开过工"，而不是
        "目前很健康"这种无据结论。
        """
        summary = self.telemetry_summary(window_seconds=window_seconds)
        if not summary.get("calls"):
            return ""
        from ..dialogue.orchestrator import telemetry_bottleneck_report
        return telemetry_bottleneck_report(summary)

    def spinning_alerts(self) -> list[dict[str, Any]]:
        return self.telemetry.guard.alerts()

    async def proxy(self, payload: dict[str, Any], transport: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        return await self.telemetry.proxy(payload, transport)

    # ---------- 判例库 ----------
    def rubrics(self, active_only: bool = True) -> list[dict[str, Any]]:
        return self.evaluator.rubrics.list(active_only=active_only)

    def deactivate_rubric(self, rubric_id: str) -> bool:
        return self.evaluator.rubrics.deactivate(rubric_id)

    def close(self) -> None:
        self.ledger.close()
        self.telemetry.close()
        self.evaluator.close()

    def __enter__(self) -> "ExecutiveService":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


__all__ = ["ExecutiveService"]
