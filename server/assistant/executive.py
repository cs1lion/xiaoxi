"""内阁总管门面：把慢环调度、观测网关、自进化初审和督办台账接在一起。

这一层是唯一同时握着四件东西的地方，因此它也只做"接线 + 读数"：
台前怎么说由 orchestrator 决定，抽屉怎么画由 web 决定，这里只给数据。
任何读失败都降级为空列表/空统计，绝不让抽屉把主页面带崩。
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any, Callable

from ..interaction.threads import CommissionStore
from .dispatcher import IN_FLIGHT_STATUSES, Dispatcher, roster_agents
from .durable_state import DurableApprovals
from .evaluator import Evaluator, load_roster
from .telemetry import SpinningGuard, TelemetryGateway, TelemetryStore

DEFAULT_TELEMETRY_WINDOW = 24 * 60 * 60

RESTART_QUESTION = "服务重启过，这单停在半路，还没跑完。要我重跑吗？"
NO_WORKER_QUESTION = "这单没人能接：花名册上的 Worker 都满载了。换将，还是等一轮？"
FALLBACK_FEEDBACK = "上一版没过初审，按密件里的验收条件逐条改，改完再交。"


class ExecutiveService:
    def __init__(self, directory, *, llm: Any = None, notes: Any = None, threads: Any = None,
                 preference_store: Any = None, roster: dict[str, Any] | None = None,
                 clock: Callable[[], float] = time.time, commissions_path: str | None = None,
                 roster_loader: Callable[[], dict[str, Any]] | None = None):
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
        # 花名册热重载：生产环境由 server.config.agents_roster_cfg 注入，
        # 隔离测试不注入，回落到启动时读的那一份。见 docs/PROTOCOL.md §17。
        self.roster_loader = roster_loader
        self.dispatcher = Dispatcher(commissions=self.ledger, evaluator=self.evaluator,
                                     roster=self.evaluator.roster,
                                     memory_provider=self._memory_provider(notes, threads, preference_store),
                                     clock=clock, telemetry=self.telemetry)
        # 收口只允许发生一次：重派出去的单会变成"在跑"，再收一次会把活着的单
        # 当成半路的单掐掉。
        self._recovery_done = False
        # Shadow 模式：sidecar 照跑，结果只落日志。None 表示没开。
        self.shadow = None
        self._shadow_tasks: set[asyncio.Task] = set()

    @property
    def roster(self) -> dict[str, Any]:
        """当前花名册。配了 loader 就每次现取，改配置无需重启。"""
        if self.roster_loader is None:
            return self.evaluator.roster
        try:
            fresh = self.roster_loader()
        except Exception:
            return self.evaluator.roster
        return fresh if isinstance(fresh, dict) and fresh else self.evaluator.roster

    def reload_roster(self) -> bool:
        """把最新花名册同步给 evaluator 与 dispatcher（热重载的落点）。"""
        fresh = self.roster
        if not fresh or fresh is self.evaluator.roster:
            return False
        # YAML 里没有 endpoint / token / endpoint_provider——它们是 bind_durable
        # 在运行时注入的。整份替换会把 durable 单变成"没配 endpoint"，下一次
        # 派单直接 BLOCKED。只迁移运行时字段，不动 adapter：用户把某条从
        # durable 改回 cli_subproc 时，那次回退必须生效。
        previous = {str(item.get("id")): item
                    for item in (self.evaluator.roster.get("agents") or [])
                    if isinstance(item, dict)}
        for agent in fresh.get("agents") or []:
            if not isinstance(agent, dict) or str(agent.get("adapter")) != "durable":
                continue
            old = previous.get(str(agent.get("id")))
            if not isinstance(old, dict) or str(old.get("adapter")) != "durable":
                continue
            for key in ("endpoint", "token", "endpoint_provider", "workspace_root"):
                if key in old and key not in agent:
                    agent[key] = old[key]
        self.evaluator.roster = fresh
        self.evaluator.thresholds = dict(fresh.get("eval_thresholds") or {})
        self.dispatcher.roster = fresh
        return True

    # ---------- 记忆注入 ----------
    def bind_durable(self, config: dict[str, Any], *, apply_to: list[str] | None = None,
                     endpoint_provider: Callable[[], str | None] | None = None) -> int:
        """把执行内核的端点与令牌写进花名册里 `adapter: durable` 的条目。

        幂等，返回被绑定的 Worker 数。没绑定时派单不会静默改派：适配器会抛
        AdapterError，那一单转 BLOCKED 并明说执行内核不可用。

        `endpoint_provider` 是"内核现在在哪"的活查询。内核被硬杀后监督器换
        端口重起，写死的 endpoint 会让每一次重试都连到死端口；提供者让适配器
        每次派单前重新取地址。返回 None（内核没起来）时适配器照旧抛错。
        """
        bound = 0
        for key in ("agents", "workers"):
            entries = self.evaluator.roster.get(key)
            if not isinstance(entries, list):
                continue
            for worker in entries:
                if not isinstance(worker, dict):
                    continue
                # switch_agents 让配置直接把指定 Worker 切到 durable；不写 YAML，
                # 回退就是改回配置，改坏了也不会留下半套花名册。
                if str(worker.get("adapter")) == "durable" or (
                        apply_to and str(worker.get("id")) in apply_to):
                    # 被 switch_agents 切过来的 Worker 原本是 cli_subproc，写盘根
                    # 写在 env.AQI_WORKSPACE_ROOT 里，没有顶层 workspace_root。
                    # 不补这一项，durable 适配器算不出沙箱，sidecar 的 write_file
                    # 会拒绝一切写入——切换后看着能跑，成果只能是一段文字。
                    own_root = str(
                        worker.get("workspace_root")
                        or (worker.get("env") or {}).get("AQI_WORKSPACE_ROOT")
                        or "").strip()
                    # A worker-specific root wins over the roster-wide default;
                    # otherwise every switched worker would share one directory.
                    worker.update({
                        k: v for k, v in config.items()
                        if v not in (None, "") and not (k == "workspace_root" and worker.get("workspace_root"))
                    })
                    if not worker.get("workspace_root") and own_root:
                        worker["workspace_root"] = own_root
                    worker["adapter"] = "durable"
                    if endpoint_provider is not None:
                        worker["endpoint_provider"] = endpoint_provider
                    bound += 1
        return bound

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
        return {"commission": commission, "packet": packet.as_stored()}

    def start(self, commission_id: str) -> dict[str, Any]:
        return self.dispatcher.start(commission_id)

    async def delegate(self, text: str, *, thread_id: str = "",
                       acceptance: Any = None) -> dict[str, Any]:
        """后台接单：open -> dispatch -> start -> 驱动执行体跑完。

        执行所有权在这里：正常委派由服务端自己驱动到终态，调用方不需要
        （也不能）只派不跑。任何一步失败都只在台账里留痕，不向外抛——
        语音轮次早就说完"接单了"，不该被慢环拖住，更不该被它带崩。

        初审打回后自动重做，但重做次数与 Evaluator.MAX_RETRY 同一个上限：
        每次重做前把上一轮的 scolding 写回密件（Worker 下一轮 prepare_context
        就能看到），状态转出 running（ready_to_report / blocked / done）或
        重做次数用尽就停。循环还额外有 retries 计数兜底，即使台账没有正常
        累计重试次数也不会无限转。
        """
        title = (text or "").strip()[:80] or "未命名督办"
        commission = self.dispatcher.open(title, text or "", thread_id)
        try:
            self.dispatcher.dispatch(commission["id"], intent=text or "", acceptance=acceptance)
            self.dispatcher.start(commission["id"])
            commission = await self.drive(commission["id"])
        except LookupError:
            # 选不出将：先承认进了组密件阶段，再带一句请示转 BLOCKED。
            # 只用协议 §19 里写明的迁移，不为了顺手新开一条边。
            self.ledger.transition(commission["id"], "briefing")
            commission = self.dispatcher.block(commission["id"], NO_WORKER_QUESTION)
        except Exception as exc:  # A broken dispatch must never break the voice turn.
            current = self.ledger.get(commission["id"])
            if current["status"] == "drafting":
                current = self.ledger.transition(commission["id"], "briefing")
            if current["status"] in ("briefing", "running", "auditing"):
                self.ledger.transition(commission["id"], "blocked",
                                       blocked_question=f"派单没组成：{type(exc).__name__}。要我再试一次吗？")
            commission = self.ledger.get(commission["id"])
        self._start_shadow(commission)
        return {"commission_id": commission["id"], "status": commission["status"],
                "agent_id": commission["agent_id"], "agent_name": self._agent_name(commission["agent_id"])}

    def _start_shadow(self, commission: dict[str, Any]) -> None:
        if self.shadow is None:
            return
        task = asyncio.create_task(self._run_shadow(commission))
        self._shadow_tasks.add(task)
        task.add_done_callback(self._shadow_tasks.discard)

    async def _run_shadow(self, commission: dict[str, Any]) -> None:
        try:
            await self.shadow(commission)
        except Exception:
            return

    async def _drive(self, commission_id: str) -> None:
        """驱动执行体跑完这一单。

        失败已经在台账里转成 BLOCKED 并留下请示，这里不再外抛：调用方（语音
        后台任务 / REST 路由）没有能力处理慢环故障，重复抛一次只是把栈打厚。
        """
        try:
            await self.run_commission(commission_id)
        except Exception:
            return

    async def drive(self, commission_id: str) -> dict[str, Any]:
        """Drive one already-started commission to a stable state.

        The C2 internal dispatch route starts the commission and hands it to this
        method in a background task, so the tool can return a commission id
        immediately. Retry policy stays identical to `delegate`.
        """
        await self._drive(commission_id)
        commission = self.ledger.get(commission_id)
        retries = 0
        while (commission["status"] == "running"
               and int(commission["retry_count"]) <= self.evaluator.MAX_RETRY
               and retries < self.evaluator.MAX_RETRY):
            retries += 1
            audit = commission.get("audit") or {}
            feedback = str(audit.get("scolding") or FALLBACK_FEEDBACK)
            self.dispatcher.retry_with_feedback(commission["id"], feedback)
            await self._drive(commission["id"])
            commission = self.ledger.get(commission["id"])
        return commission

    def _agent_name(self, agent_id: str) -> str:
        for worker in roster_agents(self.evaluator.roster):
            if isinstance(worker, dict) and str(worker.get("id")) == str(agent_id):
                return str(worker.get("name") or agent_id)
        return ""

    def adapter_types(self) -> list[str]:
        return self.dispatcher.available_adapter_types()

    def adapter_bindings(self) -> list[dict[str, Any]]:
        """每个 Agent 当前绑的适配器（只回类型名，不回配置值）。"""
        bindings = []
        for entry in roster_agents(self.roster):
            if not isinstance(entry, dict) or not entry.get("id"):
                continue
            try:
                described = self.dispatcher.adapter_for(str(entry["id"])).describe()
            except Exception as exc:
                described = {"adapter": None, "error": type(exc).__name__}
            bindings.append({"agent_id": str(entry["id"]), "name": str(entry.get("name") or ""),
                             **described})
        return bindings

    def adapter_for(self, agent_id: str) -> dict[str, Any]:
        """观测用：某个 Agent 绑的是哪个适配器。只回类型名，不回配置值。"""
        return self.dispatcher.adapter_for(agent_id).describe()

    async def run_commission(self, commission_id: str) -> dict[str, Any]:
        """驱动执行体跑完这一单：适配器事件流 -> 遥测 -> 初审。"""
        return await self.dispatcher.run_commission(commission_id)

    # ---------- 台账读数 ----------
    def commission(self, commission_id: str) -> dict[str, Any]:
        return self.ledger.get(commission_id)

    def commissions(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.ledger.list_commissions(status, limit)

    def commission_events(self, commission_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return self.ledger.events(commission_id, limit)

    def open_commissions(self) -> list[dict[str, Any]]:
        return self.ledger.open_commissions()

    def recover_interrupted(self, question: str = RESTART_QUESTION,
                            durable_lookup: Callable[[str], Any] | None = None) -> dict[str, list[str]]:
        """重启收口：把半路的单变成有出路的，把没派出去的重新派一遍。

        `durable_lookup` 是执行内核的查询函数（commission_id -> 该单在内核里的
        任务，或 None）。给了就先问内核：跑在 durable 上的单，内核可能已经把活
        干完并落了成果，一律转 BLOCKED 等于把做完的活扔掉。对账规则只有一条：
        **只采纳内核已经终态的成果，永不替内核把没跑完的单判成功。**

        briefing / running / auditing 只有活着的进程能推进，重启后没人持有它们，
        而且它们算在 Worker 的并发槽里（dispatcher 的 in_flight_load）。blocked
        是这三个状态唯一共同的合法去处（协议 §19），所以只做这一条迁移，不顺手
        新开边。

        drafting 是"接了单还没派出去"：一点活没干，重派一次就是接着做。

        返回 {"blocked": [...], "resumed": [...]}。resumed 的单已经回到 running，
        调用方负责接着驱动；不驱动它也不会再卡在半路状态。一个实例只收一次，
        重复调用返回空。
        """
        if self._recovery_done:
            return {"blocked": [], "resumed": [], "adopted": []}
        blocked: list[str] = []
        resumed: list[str] = []
        adopted: list[str] = []
        for item in self.ledger.open_commissions():
            status = item.get("status")
            if status in IN_FLIGHT_STATUSES:
                if durable_lookup is not None and self._adopt_durable(item, durable_lookup):
                    adopted.append(item["id"])
                    continue
                self.dispatcher.block(item["id"], question)
                blocked.append(item["id"])
            elif status == "drafting":
                try:
                    self.dispatcher.dispatch(item["id"],
                                             intent=item.get("brief") or item.get("title") or "")
                except LookupError:
                    self.ledger.transition(item["id"], "briefing")
                    self.dispatcher.block(item["id"], NO_WORKER_QUESTION)
                    blocked.append(item["id"])
                except Exception as exc:  # 启动期的派单失败不该带崩服务
                    if self.ledger.get(item["id"])["status"] == "drafting":
                        self.ledger.transition(item["id"], "briefing")
                    self.dispatcher.block(item["id"], f"派单没组成：{type(exc).__name__}。要我再试一次吗？")
                    blocked.append(item["id"])
                else:
                    self.dispatcher.start(item["id"])
                    resumed.append(item["id"])
        self._recovery_done = True
        return {"blocked": blocked, "resumed": resumed, "adopted": adopted}

    def reconcile_durable(self, lookup: Callable[..., Any]) -> dict[str, list[str]]:
        """Periodic ledger-vs-kernel reconciliation for durable workers.

        Never promotes a commission to `done` and never invents a result:
        - terminal completed with a complete artifact -> adopt through submit;
        - terminal failed / missing -> BLOCKED with a question;
        - still running or lookup unavailable -> leave it alone.
        """
        adopted: list[str] = []
        blocked: list[str] = []
        pending: list[str] = []
        for item in self.ledger.open_commissions():
            status = str(item.get("status") or "")
            if status not in ("briefing", "running", "auditing"):
                continue
            try:
                entry = self.dispatcher.agent_entry(str(item.get("agent_id") or ""))
            except Exception:
                continue
            if str(entry.get("adapter") or "") != "durable":
                continue
            attempt = int(item.get("retry_count") or 0)
            try:
                run = lookup(str(item["id"]), attempt)
            except TypeError:
                try:
                    run = lookup(str(item["id"]))
                except Exception:
                    pending.append(str(item["id"]))
                    continue
            except Exception:
                pending.append(str(item["id"]))
                continue
            if not isinstance(run, dict):
                try:
                    self.dispatcher.block(
                        item["id"],
                        "执行内核里找不到这一单（可能被归档或重置）。要我再派一次吗？")
                    blocked.append(str(item["id"]))
                except Exception:
                    pending.append(str(item["id"]))
                continue
            if str(run.get("status")) != "terminal":
                pending.append(str(item["id"]))
                continue
            if self._adopt_durable(item, lookup):
                adopted.append(str(item["id"]))
                continue
            try:
                self.dispatcher.block(
                    item["id"],
                    "执行内核里这一单已经终止，但没有可采纳的成果。要我再派一次吗？")
                blocked.append(str(item["id"]))
            except Exception:
                pending.append(str(item["id"]))
        return {"adopted": adopted, "blocked": blocked, "pending": pending}

    def _adopt_durable(self, commission: dict[str, Any],
                       lookup: Callable[[str], Any]) -> bool:
        """内核已经把这一单干完时，采纳它的成果而不是判"重启过、重跑吧"。

        只认两件事：内核报的是终态，且它交了一个带 summary 的成果。其余情况
        （查不到、还在跑、失败了、成果不完整）一律返回 False，交给原有收口转
        BLOCKED——对账绝不替内核把没跑完的单判成功，也不自动 `done`。
        走的是 `dispatcher.submit`，初审、打回、重试与平时完全同一条路。
        """
        status = str(commission.get("status"))
        # auditing 的单已经交过差，内核里那份不是新的；briefing 的单只是还没开跑。
        if status not in ("running", "briefing"):
            return False
        # 幂等键是 `commission_id@attempt`，查询必须带同一个 attempt。只按
        # commission_id 查会落到 @0：重做过（retry_count > 0）的单在内核里叫
        # @1/@2，于是"内核已经跑完"被误判成"查不到"，成果照样被扔。
        attempt = int(commission.get("retry_count") or 0)
        try:
            run = lookup(str(commission["id"]), attempt)
        except TypeError:
            # 兼容只收一个参数的旧查询函数：行为退回改动前，不因签名差异崩掉收口。
            run = lookup(str(commission["id"]))
        except Exception:
            return False
        if not isinstance(run, dict) or str(run.get("status")) != "terminal":
            return False
        outcome = run.get("outcome") or {}
        if str(outcome.get("status")) != "completed":
            return False
        result = outcome.get("result") or {}
        artifact = result.get("artifact")
        if not isinstance(artifact, dict) or not artifact.get("summary"):
            return False
        try:
            if status == "briefing":
                self.dispatcher.start(commission["id"])
            self.dispatcher.submit(commission["id"], artifact)
        except Exception:
            return False
        return True

    async def resume(self, commission_id: str) -> None:
        """接着驱动一单已经回到 RUNNING 的督办（重启收口之后用）。"""
        await self._drive(commission_id)

    def pending_reports(self) -> list[dict[str, Any]]:
        """READY_TO_REPORT 里还没被老板看过的那几单——心流防打扰只靠这份清单。"""
        return self.ledger.list_commissions("ready_to_report", 20)

    # ---------- 老板动作 ----------
    async def answer(self, commission_id: str, answer: str = "") -> dict[str, Any]:
        """BLOCKED -> RUNNING，并驱动执行体接着跑完。

        答完不接着跑，票就又躺回 running 没人管——和"派了不跑"是同一个坑，
        只是换了个入口。返回值是驱动之后的真实状态。
        """
        commission, reply, auto = self.dispatcher.answer(commission_id, answer)
        await self._grant_durable_approvals(commission["id"])
        await self._drive(commission["id"])
        return {"commission": self.commission(commission["id"]), "reply": reply,
                "auto_answered": auto}

    async def _grant_durable_approvals(self, commission_id: str) -> None:
        """老板答复之后，把这一单还在等批准的工具放行。

        审批链原本断在最后一环：sidecar 拦下调用、让模型请示，可没有任何代码
        把老板的答复送回去，被拦的工具在 sidecar 存活期内一直拦着。这里补上
        ——先问 sidecar 在等什么，再按它报的逐项放行，不自己猜工具名。
        """
        client = DurableApprovals(self)
        try:
            pending = await client.pending(commission_id)
        except Exception:
            return
        for item in pending:
            tool = str(item.get("tool") or "")
            if not tool:
                continue
            try:
                await client.decide(commission_id, tool=tool,
                                    action_hash=str(item.get("action_hash") or ""),
                                    approved=True)
            except Exception:
                continue

    def submit(self, commission_id: str, artifact: dict[str, Any]) -> dict[str, Any]:
        usage, spinning = self.dispatcher.audit_inputs(commission_id)
        commission, verdict = self.dispatcher.submit(commission_id, artifact, usage=usage,
                                                     spinning=spinning)
        return {"commission": commission, "verdict": verdict}

    def accept(self, commission_id: str) -> dict[str, Any]:
        return self.dispatcher.accept(commission_id)

    def reassign(self, commission_id: str) -> dict[str, Any]:
        """换将重派（协议 §23.3）：BLOCKED -> RUNNING 并重新选将。"""
        return self.dispatcher.reassign(commission_id)

    def reject(self, commission_id: str, boss_feedback: str) -> dict[str, Any]:
        commission, rubric, boss_reject_count = self.dispatcher.reject(commission_id, boss_feedback)
        return {"commission": commission, "rubric": rubric, "boss_reject_count": boss_reject_count}

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
