# -*- coding: utf-8 -*-
"""契约 v2 · §25 事件协议 v2 的**线上广播层**：把 emitter 的信封接进 /ws。

与 emitter.py 的边界
--------------------
`emitter.py` 管「描述符 -> 信封」，明写不碰 WS（广播函数由调用方注入）。
本模块补「信封 -> 每个在线连接」这一段，四样东西：

- `V2BroadcastHub`        连接级 `seq` 的唯一持有者（§25.1 取舍 1）+ 扇出
- `CommissionEventBridge` 台账投影 diff -> 六类委托事件（单一订阅点）
- `validate_command` / `RequestLedger`  §25.3 五命令的校验与 request_id 幂等
- `V2CommandActions`      五命令 -> 服务端动作；失败走显式错误路径
- `publish_tool_execution` / `publish_memory_update` / `publish_presence`
  三个显式发送点（dispatcher / manager / app.py）的薄封装

纪律
----
1. **不 import app.py**（那会反向依赖）：sink 是注入的 callable，
   只要求满足 emitter 的 `Broadcast` 协议（收 dict、回 bool）。
2. 一条逻辑事件对多个连接**共享同一个 `id`**（§25.5 消费方按 id 去重），
   `seq` 按连接各自盖——连接级单调，不是全局单调。
3. 广播失败不冒泡：事件是旁路通知，状态以台账 / REST 为准（协议 §19 的
   既有口径，`commission_update` 同理）。
4. **不编造 payload**（emitter 纪律 1 的延续）：委托链路没跑过的字段
   （如 CONTRACT_PROPOSED 的 `risk`）不填，由 emitter 记进 `missing`。

规范来源：docs/PROTOCOL.md §25（事件协议 v2）；PIXEL_WORKSHOP_SPEC_V2.md §5。
"""
from __future__ import annotations

import asyncio
import contextlib
import threading
from collections import deque
from typing import Any, Awaitable, Callable

from . import events_v2 as v2
from .emitter import emit

# 广播 sink 的签名与 emitter.Broadcast 一致：收信封 dict，回 True 表示受理。
Sink = Callable[[dict[str, Any]], bool]
# 命令错误回执：连接层把它接成既有 v1 error 帧（不新造 WS 事件名）。
Reply = Callable[[str, str], Awaitable[None]]

# §25.3 CANCEL_TASK 的 reason 闭集（与前端 buildCommand 同口径）。
CANCEL_REASONS = ("user_abort", "wrong_task")


class V2BroadcastHub:
    """连接级 `seq` 的持有者与扇出器。

    `seq` 为什么在这里而不在连接对象上：emitter 纪律 3 说「seq 由调用方持有」，
    调用方是这一层。每个已附着的 sink 各有一个计数器，从 1 起严格递增
    （与 `server/task/events.py::next_seq` 的起算一致）。

    进程内另有一个**总计数器**只用于给 emitter 一个单调入参——信封真正的
    `seq` 在扇出时按连接覆盖。它不是协议概念，只保证「同一 hub 里后发布的
    事件基数更大」，便于日志排序。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        # key -> {"seq": int, "sink": Sink}。key 由连接层给（session_id 即可）。
        self._consumers: dict[Any, dict[str, Any]] = {}
        self._counter = 0

    # ---------- 连接管理 ----------

    def attach(self, key: Any, sink: Sink) -> Callable[[], None]:
        """附着一条连接的 sink，返回解除函数。重复 key 覆盖旧 sink。"""
        with self._lock:
            self._consumers[key] = {"seq": 0, "sink": sink}

        def detach() -> None:
            with self._lock:
                self._consumers.pop(key, None)

        return detach

    def consumer_count(self) -> int:
        with self._lock:
            return len(self._consumers)

    # ---------- 发布 ----------

    def publish(self, event_type: str, payload: dict[str, Any], *,
                task_id: str = "", step_id: str = "",
                session_id: str = "") -> Any:
        """发一条 v2 事件给所有在线连接（未知事件名不发不抛，见 emitter.emit）。"""
        with self._lock:
            self._counter += 1
            base_seq = self._counter
        return emit(event_type, payload or {}, seq=base_seq, broadcast=self._fanout,
                    task_id=task_id, step_id=step_id, session_id=session_id)

    def _fanout(self, envelope: dict[str, Any]) -> bool:
        """emitter.Broadcast 协议：按连接盖 seq 后逐个投递。

        返回 False 表示至少一个连接受理失败（至少一次档的事件由此让
        `emit()` 报 emitted=False）；单个连接失败不连累其它连接。
        """
        with self._lock:
            items = [(key, dict(entry)) for key, entry in self._consumers.items()]
        if not items:
            # 零连接 = 无投递对象：不算失败（状态以 REST 台账为准，§25.5 首帧）。
            return True
        ok = True
        for key, snapshot in items:
            with self._lock:
                entry = self._consumers.get(key)
                if entry is None:  # 投递途中被解除：这一条无处可去，不算失败
                    continue
                entry["seq"] += 1
                seq = entry["seq"]
            stamped = {**envelope, "seq": seq}
            try:
                delivered = bool(snapshot["sink"](stamped))
            except Exception:  # noqa: BLE001 - 一个 sink 坏了不能连累其它连接
                delivered = False
            ok = ok and delivered
        return ok


class CommissionEventBridge:
    """台账订阅者：把 `CommissionStore` 的投影 diff 成 §25 的六类委托事件。

    为什么用「单一订阅 + diff」而不是在 dispatcher 每个方法里发：台账是唯一
    真相（协议 §19），REST 手动路径、调度器重派、重启收口全都从台账的
    `_publish` 过——挂在这里意味着**没有第二条写路径能绕过事件**。

    覆盖的事件（全部从已落库的投影取材，不取不到的就留空）：

    - `TASK_STATE_CHANGE`   每次 status 变化（含委托创建 from="" -> drafting）
    - `CONTRACT_PROPOSED`   进入 briefing（派单密件落库）；重做改写密件时升版再发
    - `ASK_USER`            进入 blocked（blocked_question 即请示）
    - `VERIFICATION_REPORT` running -> auditing 且 audit 带 passed（初审落判）
    - `DELIVERY_EFFECT`     ready_to_report -> done 且非取消（交付入库）
    - `STEP_STATE_CHANGE`   step_idx 变化（执行体每交一步进展）
    """

    def __init__(self, hub: V2BroadcastHub, *, max_tracked: int = 256) -> None:
        self.hub = hub
        self._prev: dict[str, dict[str, Any]] = {}
        self._order: deque[str] = deque()
        self._max_tracked = max_tracked

    # ---------- 订阅入口 ----------

    def on_commission(self, commission: dict[str, Any]) -> None:
        """`CommissionStore.subscribe` 的回调形状。任何异常都吞掉：事件是旁路。"""
        try:
            self._handle(commission)
        except Exception:  # noqa: BLE001 - 台账回调不能被事件层带崩
            return

    # ---------- diff 与发射 ----------

    def _handle(self, commission: dict[str, Any]) -> None:
        cid = str(commission.get("id") or "")
        if not cid:
            return
        status = str(commission.get("status") or "")
        step = int(commission.get("step_idx") or 0)
        attempt = int(commission.get("retry_count") or 0)
        agent = str(commission.get("agent_id") or "")
        packet = commission.get("packet") if isinstance(commission.get("packet"), dict) else {}
        audit = commission.get("audit") if isinstance(commission.get("audit"), dict) else {}
        delivery = (commission.get("delivery")
                    if isinstance(commission.get("delivery"), dict) else {})
        prev = self._prev.get(cid)

        if prev is None:
            # 首见 = create()：委托创建本身就是一次状态迁移（from 空 -> drafting）。
            self.hub.publish(v2.TASK_STATE_CHANGE,
                             {"from": "", "to": status, "attempt": attempt},
                             task_id=cid)
        else:
            if status != prev["status"]:
                payload: dict[str, Any] = {"from": prev["status"], "to": status,
                                           "attempt": attempt}
                if status == "blocked":
                    kind = str(commission.get("blocked_kind") or "clarification")
                    payload["reason"] = kind  # blocked_kind 是台账里的真实字段
                self.hub.publish(v2.TASK_STATE_CHANGE, payload, task_id=cid)
                if status == "briefing":
                    self._emit_contract_proposed(cid, packet, version=1)
                if status == "blocked":
                    question = str(commission.get("blocked_question") or "")
                    self.hub.publish(v2.ASK_USER,
                                     {"question": question, "options": [], "missing": []},
                                     task_id=cid)
                if prev["status"] == "running" and status == "auditing" \
                        and isinstance(audit.get("passed"), bool):
                    self.hub.publish(v2.VERIFICATION_REPORT,
                                     {"passed": audit["passed"],
                                      "findings": list(audit.get("findings") or []),
                                      "criteria": verification_criteria(audit, packet)},
                                     task_id=cid)
                if status == "done" and prev["status"] != "done" \
                        and delivery and not audit.get("cancelled"):
                    # 交付入库：accept() 把 ready_to_report 归档成 done。
                    # 取消路径（audit.cancelled）不是交付，不发。
                    self.hub.publish(v2.DELIVERY_EFFECT,
                                     {"effect_id": f"{cid}:archive",
                                      "type": "archive",
                                      "target_class": "commission",
                                      "status": "done",
                                      "idempotency_key": f"{cid}:archive"},
                                     task_id=cid)
            # 重做改写密件（retry_with_feedback：auditing -> running 且 feedback 变化）
            # 是这张工作单的新版本：契约再提案、版本 = attempt + 1。
            feedback = str(packet.get("feedback") or "")
            if prev["status"] == "auditing" and status == "running" \
                    and feedback and feedback != prev.get("feedback"):
                self._emit_contract_proposed(cid, packet, version=attempt + 1)
            if step != prev["step"]:
                # 台账的 step 是「第几步」计数（§22 行协议的 step_idx），不是
                # §4.5 的 11 态名——慢环只有这个真实数据，照实发不编造状态名。
                self.hub.publish(v2.STEP_STATE_CHANGE,
                                 {"from": str(prev["step"]), "to": str(step),
                                  "agent": agent, "attempt": attempt},
                                 task_id=cid, step_id=str(step))

        self._prev[cid] = {"status": status, "step": step,
                           "feedback": str(packet.get("feedback") or "")}
        # 有界化：追过的单上限 max_tracked，超出按 FIFO 淘汰（旧单重开时首见会
        # 重发一条 TASK_STATE_CHANGE，from 为空——可接受，REST 台账仍是真相）。
        if cid not in self._order:
            self._order.append(cid)
            while len(self._order) > self._max_tracked:
                self._prev.pop(self._order.popleft(), None)

    def _emit_contract_proposed(self, cid: str, packet: dict[str, Any], *,
                                version: int) -> None:
        """§25.2 CONTRACT_PROPOSED 的公开投影（不带 memory_block / 凭据）。

        `risk` 留空：委托派单链路没有跑契约层的风险模型（`server/contract/risk.py`
        接的是 TaskContract，不是 BriefingPacket），编一个 "low" 出来比缺字段危险。
        emitter 会把它记进 missing，前端按空串展示。
        """
        goal = str(packet.get("objective") or "")
        artifact = str(packet.get("deliverable") or "")
        inputs_kind = sorted({str(ref.get("type"))
                              for ref in (packet.get("memory_refs") or [])
                              if isinstance(ref, dict) and ref.get("type")})
        highlighted = [f"{gap.get('check') or ''}:{gap.get('key') or ''}".strip(":")
                       for gap in (packet.get("contract_gaps") or [])
                       if isinstance(gap, dict)]
        criteria = []
        for index, item in enumerate(packet.get("acceptance") or []):
            if not isinstance(item, dict):
                continue
            criteria.append({"id": str(item.get("name") or f"C{index + 1}"),
                             "kind": str(item.get("kind") or "")})
        self.hub.publish(v2.CONTRACT_PROPOSED,
                         {"contract_id": cid, "version": version, "goal": goal,
                          "artifact": artifact, "inputs_kind": inputs_kind,
                          "highlighted_slots": [slot for slot in highlighted if slot],
                          "criteria": criteria,
                          "budget": dict(packet.get("budget") or {})},
                         task_id=cid)


def verification_criteria(audit: dict[str, Any],
                          packet: dict[str, Any]) -> list[dict[str, Any]]:
    """初审判据 -> §25.2 VERIFICATION_REPORT 的 criteria。

    L1 检查（`packet.budget.eval_commands`）是确定性断言，一条一项；
    判不了的命令名照列（`passed` 按有没有触发 finding 推）；L2 启发式
    finding（命令名对不上的）单列成 semantic 未过项。**全部从 verdict 真实
    字段推导，不编通过**：命令没跑就是没跑，finding 的 rule_id 就是证据索引。
    """
    commands = [str(name) for name in ((packet.get("budget") or {}).get("eval_commands")
                                       or [])]
    findings = [item for item in (audit.get("findings") or []) if isinstance(item, dict)]
    failed_rules = {str(item.get("rule_id") or "") for item in findings}
    criteria: list[dict[str, Any]] = []
    for name in commands:
        criteria.append({"id": name, "kind": "deterministic",
                         "passed": name not in failed_rules})
    for item in findings:
        rule_id = str(item.get("rule_id") or "")
        if rule_id in commands or not rule_id:
            continue
        criteria.append({"id": rule_id, "kind": "semantic", "passed": False,
                         "evidence": str(item.get("detail") or "")[:200]})
    return criteria


# ---------------------------------------------------------------- §25.3 命令

def validate_command(event: dict[str, Any]) -> str:
    """§25.3 五命令校验：返回空串 = 合法，否则为给人读的错误消息。

    口径与前端 `web/events-v2.js::buildCommand` 一致（必填缺一不可、
    confirmed_slots 字符串数组、answer 字符串、reason 闭集），另加服务端
    必查的字符串类型（前端构造保证，网络对面不保证）。
    """
    ctype = event.get("type")
    if ctype not in v2.COMMAND_TYPES:
        return f"未知的 v2 命令 {ctype!r}"
    for name in v2.COMMAND_FIELDS[ctype]:
        value = event.get(name)
        if value is None or value == "":
            return f"{ctype} 缺必填字段 {name}"
    if not all(isinstance(event.get(name), str) and event[name]
               for name in ("task_id", "request_id")):
        return "task_id / request_id 必须是非空字符串"
    if ctype == v2.CMD_CONFIRM_CONTRACT:
        if not isinstance(event.get("confirmed_slots"), list) \
                or any(not isinstance(slot, str) for slot in event["confirmed_slots"]):
            return "CONFIRM_CONTRACT 的 confirmed_slots 必须是字符串数组"
        version = event.get("version")
        if isinstance(version, bool) or not isinstance(version, int):
            return "CONFIRM_CONTRACT 的 version 必须是整数"
    if ctype == v2.CMD_ANSWER and not isinstance(event.get("answer"), str):
        return "ANSWER 的 answer 必须是字符串"
    if ctype == v2.CMD_CANCEL_TASK:
        reason = event.get("reason")
        if reason is not None and reason not in CANCEL_REASONS:
            return "CANCEL_TASK 的 reason 只能是 user_abort 或 wrong_task"
    if ctype == v2.CMD_APPROVE_SIDE_EFFECT and not isinstance(event.get("effect_id"), str):
        return "APPROVE_SIDE_EFFECT 的 effect_id 必须是字符串"
    if ctype == v2.CMD_RETRY_STEP and not isinstance(event.get("step_id"), str):
        return "RETRY_STEP 的 step_id 必须是字符串"
    option_index = event.get("option_index")
    if option_index is not None and (isinstance(option_index, bool)
                                     or not isinstance(option_index, int)):
        return "ANSWER 的 option_index 必须是整数"
    return ""


class RequestLedger:
    """§25.3 `request_id` 幂等键的进程内登记表（有界 FIFO）。

    网络重发同一命令不得产生第二次副作用：首次见到返回 True 并登记，
    重放返回 False。有界是为了长跑页面不攒无界内存；溢出淘汰最旧的一条
    （极端情况下重放一条很久以前的命令会被再执行——单用户本地系统的
    可接受取舍，与前端 `MAX_SEEN_IDS` 同思路）。
    """

    def __init__(self, capacity: int = 512) -> None:
        self._seen: dict[str, None] = {}
        self._order: deque[str] = deque()
        self._capacity = capacity

    def claim(self, request_id: str) -> bool:
        if request_id in self._seen:
            return False
        self._seen[request_id] = None
        self._order.append(request_id)
        while len(self._order) > self._capacity:
            self._seen.pop(self._order.popleft(), None)
        return True


class V2CommandActions:
    """§25.3 五命令 -> 服务端动作。前置不满足 / 动作失败走显式错误回执，
    不静默落空。

    动作映射（全部接既有服务端能力，executive 门面）：

    | 命令 | 动作 | 前置 |
    |---|---|---|
        | CONFIRM_CONTRACT | drafting/briefing 单放行（dispatch+start+后台驱动）；在链单幂等确认 | contract_id/version 与台账一致 |
        | ANSWER | `executive.answer`（解阻塞并驱动跑完，后台任务） | 单在 blocked |
        | CANCEL_TASK | `dispatcher.cancel`（reason 进 audit.cancel_reason，真实字段） | 单存在 |
    | APPROVE_SIDE_EFFECT | blocked(approval) 单走 answer 链路（自动放行 durable 待批工具） | blocked_kind == approval |
    | RETRY_STEP | blocked 单解阻塞重跑（慢环以单为重做单位，step_id 记进答复） | 单在 blocked |

    回执用既有 v1 `error` 帧形状（code/message），不新造 WS 事件名；
    code 前缀 `v2_` 便于前端与日志归类。
    """

    def __init__(self, executive: Any) -> None:
        self.executive = executive
        self.requests = RequestLedger()
        self._tasks: set[asyncio.Task] = set()

    async def handle(self, command: dict[str, Any], reply: Reply) -> None:
        error = validate_command(command)
        if error:
            await reply("v2_command_invalid", error)
            return
        if not self.requests.claim(str(command["request_id"])):
            # §25.3 幂等重放：不产生第二次副作用，也不再回包（首次的副作用
            # 与事件早已发生，重复驱动反而制造第二份真相）。
            return
        ctype = str(command["type"])
        try:
            handler = self._HANDLERS[ctype]
            await handler(self, command, reply)
        except KeyError:
            await reply("v2_task_not_found", f"找不到这单：{command.get('task_id')}")
        except LookupError as exc:
            # KeyError 之外的那支 LookupError（如 dispatch 选不出将）：单还在
            # 原地，显式回绝比笼统的「执行失败」可诊断。
            await reply("v2_command_rejected", str(exc) or "慢环放行失败，这单没动。")
        except ValueError as exc:
            await reply("v2_command_rejected", str(exc))
        except Exception:  # noqa: BLE001 - 慢环动作失败要明说，不能静默落空
            await reply("v2_command_failed", "服务端执行失败，请稍后再试。")

    # ---------- 各命令 ----------

    async def _confirm_contract(self, command: dict[str, Any], reply: Reply) -> None:
        cid = str(command["task_id"])
        commission = self.executive.commission(cid)
        if str(command["contract_id"]) != cid:
            raise ValueError("contract_id 与当前委托不一致")
        expected = int(commission.get("retry_count") or 0) + 1
        if int(command["version"]) != expected:
            raise ValueError(f"契约版本不一致：当前是 v{expected}")
        status = str(commission.get("status"))
        if status == "drafting":
            # 确认契约 = 放行：dispatch 组密件（drafting -> briefing，正是 bridge
            # 发 CONTRACT_PROPOSED 的那次迁移），然后走同一条放行链。
            intent = str(commission.get("brief") or commission.get("title") or "")
            self.executive.dispatch(cid, intent=intent)
            status = "briefing"
        if status == "briefing":
            # briefing -> running -> 后台驱动到终态：与 begin_commission 同一条
            # 链，只是入口从「自动放行」换成「老板确认」。停在 briefing 的单
            # （REST 派过没 start 的）也由确认放行，不留原地。
            self.executive.start(cid)
            self._spawn(self.executive.drive(cid))
        elif status == "done":
            raise ValueError("这单已收口，没有待确认的契约")
        # running/auditing/ready_to_report/blocked：创建时已自动放行（delegate
        # 链路），再确认一次是幂等 no-op，状态事件照旧从台账流。

    async def _answer(self, command: dict[str, Any], reply: Reply) -> None:
        cid = str(command["task_id"])
        commission = self.executive.commission(cid)
        if str(commission.get("status")) != "blocked":
            raise ValueError("这单不在等回答")
        # answer() 会驱动执行体跑完——可能跑满一个 Worker 超时，不能占 /ws
        # 接收循环，起后台任务（与 delegate 的后台语义一致）。
        self._spawn(self.executive.answer(cid, str(command["answer"])))

    async def _cancel_task(self, command: dict[str, Any], reply: Reply) -> None:
        cid = str(command["task_id"])
        commission = self.executive.commission(cid)
        reason = str(command.get("reason") or "user_abort")
        # reason 要进 audit.cancel_reason（dispatcher.cancel 的真实字段），但门面
        # ExecutiveService.cancel 不带 reason 参数——executive.py 是项目红线不改，
        # 照 durable_api.py 的既有先例直达 dispatcher：能力已存在，只是没从门面过。
        self.executive.dispatcher.cancel(cid, reason=reason)
        # cancel 幂等（已 done 原样返回），台账迁移自然流出 TASK_STATE_CHANGE。

    async def _approve_side_effect(self, command: dict[str, Any], reply: Reply) -> None:
        cid = str(command["task_id"])
        commission = self.executive.commission(cid)
        if (str(commission.get("status")) != "blocked"
                or str(commission.get("blocked_kind")) != "approval"):
            raise ValueError("这单没有在等的副作用批准")
        # 批准 = 解除阻塞 + 放行 durable 在等的工具：answer 链路自带
        # _grant_durable_approvals。慢环目前一单至多挂一个审批阻塞，effect_id
        # 没有可核对的登记表——待效果登记表（§5.2 DELIVERY_EFFECT 的执行器侧）
        # 出现后再加校验，先不编一个比对。
        self._spawn(self.executive.answer(cid, "批准副作用"))

    async def _retry_step(self, command: dict[str, Any], reply: Reply) -> None:
        cid = str(command["task_id"])
        commission = self.executive.commission(cid)
        if str(commission.get("status")) != "blocked":
            raise ValueError("这单不在可重试的阻塞态")
        # 慢环的重做以单为最小单位（dispatcher 没有单步重试入口）；解阻塞
        # 即整单带着上下文重跑，step_id 写进答复让 Worker 能定位。
        self._spawn(self.executive.answer(cid, f"重试步骤 {command['step_id']}"))

    _HANDLERS = {
        v2.CMD_CONFIRM_CONTRACT: _confirm_contract,
        v2.CMD_ANSWER: _answer,
        v2.CMD_CANCEL_TASK: _cancel_task,
        v2.CMD_APPROVE_SIDE_EFFECT: _approve_side_effect,
        v2.CMD_RETRY_STEP: _retry_step,
    }

    # ---------- 后台任务 ----------

    def _spawn(self, coroutine: Any) -> None:
        """长动作起后台任务；异常吞掉（慢环失败已在台账里转 BLOCKED）。"""
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)

        def _settled(done: asyncio.Task) -> None:
            self._tasks.discard(done)
            if not done.cancelled():
                with contextlib.suppress(Exception):
                    done.exception()

        task.add_done_callback(_settled)

    async def close(self) -> None:
        """服务关停时收掉还在跑的后台驱动（answer/drive 可能挂着 Worker）。"""
        for task in list(self._tasks):
            task.cancel()
        for task in list(self._tasks):
            with contextlib.suppress(BaseException):
                await task
        self._tasks.clear()


# ---------------------------------------------------------------- 显式发送点

def publish_tool_execution(*, task_id: str, tool: str, status: str,
                           steps_done: int | None = None,
                           steps_total: int | None = None,
                           detail: str = "") -> None:
    """Worker 适配器进展 -> TOOL_EXECUTION（§25.2 至多一次，可丢）。

    dispatcher 的 progress 分支调用：一条进展就是一次真实工具执行的可观测
    信号（§22 行协议的 progress 事件），不发不阻塞任务。
    """
    payload: dict[str, Any] = {"tool": str(tool), "status": str(status)}
    if steps_done is not None:
        payload["steps_done"] = int(steps_done)
    if steps_total is not None:
        payload["steps_total"] = int(steps_total)
    if detail:
        payload["detail"] = str(detail)[:200]
    v2_hub.publish(v2.TOOL_EXECUTION, payload, task_id=task_id)


def publish_memory_update(*, action: str, memory_ref: str, source: str,
                          confidence: float, provenance: str) -> None:
    """记忆写入 -> MEMORY_UPDATE（§25.2 至少一次）。

    action 取值照规范 §5.6 的语义表：`corrected`（用户纠正）、`deleted`
    （物理硬删）、`promoted`（显式 stated 写入 / 来源更强的 upsert）。
    provenance 是字符串（前端 `applyMemory` 的消费口径），带上会话来源。
    """
    v2_hub.publish(v2.MEMORY_UPDATE,
                   {"action": str(action), "memory_ref": str(memory_ref),
                    "source": str(source), "confidence": float(confidence),
                    "provenance": str(provenance)[:200]})


def publish_presence(state: str, *, session_id: str = "") -> None:
    """会话层在场状态 -> AGENT_PRESENCE（§25.2 至多一次）。四态之外不发。"""
    if state not in v2.AGENT_PRESENCE_STATES:
        return
    v2_hub.publish(v2.AGENT_PRESENCE, {"state": state}, session_id=session_id)


# 进程级单例：app.py 给 /ws 连接附着 sink、startup 订阅台账；dispatcher /
# manager / 会话层的显式发送点都往这里发。测试里可另建实例做隔离。
v2_hub = V2BroadcastHub()
v2_bridge = CommissionEventBridge(v2_hub)
