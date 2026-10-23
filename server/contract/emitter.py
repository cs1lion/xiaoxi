# -*- coding: utf-8 -*-
"""契约 v2 · §5 事件协议 v2 的**发射层**：把状态机迁移变成一条可发的事件。

为什么需要它
------------
`server/task/machine.py:10` 明写「**本模块不发事件**」——迁移表里的
`TransitionResult.events` 只是「该发什么」的字符串描述符。那个边界是对的：
状态机只管迁移合法性，不该知道 WS、JSONL、广播对象。

本模块补的是「描述符 → 一条真正的事件信封」这一段，且**仍然不发 WS**：
广播函数由调用方注入（`broadcast` 参数）。理由：

- 本层可单测（不连 socket、不起服务）
- 线上广播由 `app.py` 决定，它知道有哪些连接
- 想改成写 JSONL 或只做 dry-run，不用改本模块

三条纪律
--------
1. **不猜 payload**。每类事件的载荷从迁移结果与调用方给的 facts 里取，
   取不到就留空并记进 `missing`，**不编造**。§5.2 的载荷是契约。
2. **投递保障分两档**（§5.2）：`AT_LEAST_ONCE` 的广播失败要能让调用方知道；
   `AT_MOST_ONCE` 的失败直接吞掉（少闪几下无所谓）。
3. **`seq` 由调用方持有**（§5.1 取舍 1：连接级单调）。本模块不维护全局状态，
   那会变成第二个真相源。

规范来源：`docs/PIXEL_WORKSHOP_SPEC_V2.md` §5；协议文档第 25 节。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable

from . import events_v2 as v2

# 广播函数的签名：收到一个 dict（§5.1 信封形状），返回 True 表示送达。
# 返回 False 或抛异常都算失败；调用方按投递保障决定要不要在意。
Broadcast = Callable[[dict[str, Any]], bool]


@dataclass
class EmitResult:
    """一次发射的结果。`missing` 是被丢弃/留空的字段，**用于审计**。

    `emitted` 为 False 时 `reason` 说明为什么没发（未知事件、无 payload 等）。
    """

    emitted: bool = False
    envelope: dict[str, Any] = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    reason: str = ""
    delivery: str = ""


def _now_ms() -> int:
    return int(time.time() * 1000)


def _next_event_id() -> str:
    import uuid
    return uuid.uuid4().hex


def build_envelope(event_type: str, payload: dict[str, Any], *, seq: int,
                   task_id: str = "", step_id: str = "",
                   session_id: str = "", turn_id: str = "",
                   ts: int | None = None) -> dict[str, Any]:
    """构造 §5.1 的信封。

    **可选字段为空时不出现在结果里**（而不是写 `None`）——§5.1 的
    `task_id?` 是可选，不是可空。这一条与 `server/task/events.py` 的
    `EventEnvelope.to_mapping()` 保持同一语义，避免两处写法漂移。
    """
    if not event_type:
        raise ValueError("事件缺少 type")
    envelope: dict[str, Any] = {
        "v": v2.ENVELOPE_VERSION,
        "id": _next_event_id(),
        "seq": int(seq),
        "ts": _now_ms() if ts is None else int(ts),
        "type": event_type,
    }
    for key, value in (("task_id", task_id), ("step_id", step_id),
                       ("session_id", session_id), ("turn_id", turn_id)):
        if value:
            envelope[key] = value
    envelope["payload"] = payload if payload is not None else {}
    return envelope


def missing_fields(event_type: str, payload: dict[str, Any]) -> tuple[str, ...]:
    """§5.2 规定的必填字段里，哪些没给。

    只查 `PAYLOAD_FIELDS` 里列出的必填项；可选字段（如 `station?`、
    `steps_done?`）不查——规范用 `?` 标了它们是可选的。
    """
    required = v2.PAYLOAD_FIELDS.get(event_type, ())
    if not required:
        return ()
    return tuple(name for name in required if name not in payload
                 or payload[name] is None)


def emit(event_type: str, payload: dict[str, Any], *, seq: int,
         broadcast: Broadcast | None = None,
         task_id: str = "", step_id: str = "",
         session_id: str = "", turn_id: str = "",
         ts: int | None = None) -> EmitResult:
    """发射一条 v2 事件。

    - 未知事件：不发，返回 `emitted=False` 并说明原因（不抛异常——
      一条事件名写错不该让整个任务挂掉）。
    - 必填字段缺失：**照发**，但把缺失项记进 `missing`。理由：§5.2 的
      「至少一次」比「字段齐全」更硬——少一个字段是画面缺一块，
      不发是画面与真相不一致。缺失项留给调用方决定是补还是报错。
    - `broadcast` 为 None：只构造信封不广播（dry-run，供单测与回放用）。
    """
    if event_type not in v2.EVENT_TYPES:
        return EmitResult(emitted=False, reason=f"未知的 v2 事件 {event_type!r}")

    payload = dict(payload or {})
    absent = missing_fields(event_type, payload)
    envelope = build_envelope(event_type, payload, seq=seq, task_id=task_id,
                              step_id=step_id, session_id=session_id,
                              turn_id=turn_id, ts=ts)
    delivery = v2.delivery_guarantee(event_type)

    if broadcast is None:
        return EmitResult(emitted=True, envelope=envelope, missing=absent,
                          reason="未注入广播函数（dry-run）", delivery=delivery)

    try:
        ok = bool(broadcast(envelope))
    except Exception as exc:  # noqa: BLE001 - 广播失败不该冒泡打断任务
        ok = False
        reason = f"广播抛异常：{type(exc).__name__}"
    else:
        reason = "" if ok else "广播返回 False"

    if not ok and delivery == "at_least_once":
        # 至少一次的丢了会让画面与真相不一致，调用方必须知道
        return EmitResult(emitted=False, envelope=envelope, missing=absent,
                          reason=reason or "至少一次的事件未送达", delivery=delivery)
    # 至多一次的失败直接算发过（§5.2：少闪几下无所谓）
    return EmitResult(emitted=True, envelope=envelope, missing=absent,
                      reason="" if ok else (reason or "可丢事件未送达"),
                      delivery=delivery)


def emit_transition(result: Any, *, seq: int, broadcast: Broadcast | None = None,
                    task_id: str = "", step_id: str = "",
                    session_id: str = "", turn_id: str = "",
                    facts: Any = None, ts: int | None = None) -> list[EmitResult]:
    """把一次状态机迁移（`TransitionResult`）里声明的每个事件发出去。

    `result.events` 是 `machine.py` 声明的描述符元组（可能为空、可能多个）。
    载荷从 `result` 与 `facts` 里取：

    - `from` / `to` 来自 `result.source` / `result.target`
    - `attempt` 来自 `result.state.attempt`（Task）或 `retries`（Step）
    - 其余字段（`agent` / `station` / `reason` / `risk` …）**只能从 facts 取**；
      取不到就留空并记进 `missing`——**不编造**。

    返回每个事件的 `EmitResult`，顺序与 `result.events` 一致。
    """
    declared = tuple(getattr(result, "events", ()) or ())
    if not declared:
        return []

    results: list[EmitResult] = []
    for index, event_type in enumerate(declared):
        payload = _payload_for(event_type, result, facts)
        results.append(emit(
            event_type, payload, seq=seq + index, broadcast=broadcast,
            task_id=task_id, step_id=step_id, session_id=session_id,
            turn_id=turn_id, ts=ts))
    return results


def _payload_for(event_type: str, result: Any, facts: Any) -> dict[str, Any]:
    """从迁移结果与 facts 组装载荷。**取不到的字段不填，不猜。**

    这是本模块最需要谨慎的地方：编一个 `risk="low"` 出来会让画面显示
    「低风险」而真实风险可能是 high——那比不发这条事件更危险。
    """
    payload: dict[str, Any] = {}

    def take(name: str, *sources: Any) -> None:
        """按顺序从 sources 里取第一个非空值，取到就放进 payload。"""
        for source in sources:
            if source is None:
                continue
            value = source.get(name) if isinstance(source, dict) else getattr(
                source, name, None)
            if value not in (None, ""):
                payload[name] = value
                return

    if event_type in (v2.TASK_STATE_CHANGE, v2.STEP_STATE_CHANGE):
        take("from", result)
        # result 的字段名叫 source，不是 from
        source_value = getattr(result, "source", "")
        if source_value:
            payload["from"] = source_value
        take("to", result)
        target_value = getattr(result, "target", "")
        if target_value:
            payload["to"] = target_value
        # attempt：Task 用 attempt，Step 用 retries
        state = getattr(result, "state", None)
        take("attempt", state)
        take("retries", state)
        if "attempt" not in payload and "retries" in payload:
            payload["attempt"] = payload.pop("retries")
        # 其余只能从 facts 取
        for name in ("agent", "station", "reason", "resume_to", "risk"):
            take(name, facts, state)

    elif event_type == v2.CONTRACT_PROPOSED:
        for name in ("contract_id", "version", "goal", "artifact", "risk",
                     "highlighted_slots", "criteria", "budget",
                     "deliver_to_shown"):
            take(name, facts)

    elif event_type == v2.ASK_USER:
        for name in ("question", "options", "missing", "deadline"):
            take(name, facts)

    elif event_type == v2.TOOL_EXECUTION:
        for name in ("tool", "status", "steps_done", "steps_total", "detail"):
            take(name, facts)

    elif event_type == v2.VERIFICATION_REPORT:
        for name in ("passed", "findings", "criteria"):
            take(name, facts)

    elif event_type == v2.DELIVERY_EFFECT:
        for name in ("effect_id", "type", "target_class", "status",
                     "idempotency_key"):
            take(name, facts)

    elif event_type == v2.MEMORY_UPDATE:
        for name in ("action", "memory_ref", "source", "confidence",
                     "provenance"):
            take(name, facts)

    elif event_type == v2.AGENT_PRESENCE:
        take("state", facts)

    return payload
