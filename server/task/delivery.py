"""交付与 `effect_log` 幂等（规范 §4.9 / §5.2，M1 第 6 步）。

职责
----
把一组已声明的副作用执行掉，并把每次的结果记成 `EffectRecord`，产出一条
可以直接喂给 `TaskFacts.effect_records` 的完整 `effect_log`。

**执行器是注入的**。本模块不知道「怎么发消息 / 怎么覆盖文件 / 怎么调远端」——
那是 Worker 适配器的事。这里只回答「这条副作用该不该执行、执行完了记什么」。
第 6 步还没有真实执行器，所以这是一个端口：传函数、传类实例、传 lambda 都行。

边界（与 `server/task/` 其余部分一致）
--------------------------------------
- 零业务 import：只 import 标准库与本包内的类型。
- 零 IO：不读文件、不开 socket、不连库。
- 零时钟：时间戳由调用方传入（`now_ms`），同一次交付的所有记录用同一个值。
- 不发事件：`DELIVERY_EFFECT` 上 WS 属第 8 步，有 `[PROTOCOL_REQUEST]` 门。

幂等的边界
----------
幂等靠 `idempotency_key`：已经有一条 `status=done` 的 key，第二次不再调执行器，
只记一条 `replayed`（SM-11）。**只有 `failed` 记录的 key 会重试**——失败的那次没有
产生副作用，重试不违反 §4.8 的「派发后不得重放」（那条管的是**不知道有没有执行**，
不是**确定失败**）。

已知缺口（本轮实测暴露，报告里单列，不在本模块内自行修补）
----------------------------------------------------------
1. `EffectRecord` 没有 `detail` 字段，失败原因只能活在 `DeliveryStep.error` 里，
   落盘时会丢。
2. §5.2 的 `DELIVERY_EFFECT` 载荷要求 `effect_id`，`EffectRecord` 没有这个字段，
   第 8 步发事件时构造不出来。
3. 执行器抛异常时副作用**可能已经发生**（超时 ≠ 没执行），但交付层只有
   done/failed/replayed 三值，没有 unknown，所以这里只能记 failed。

为什么这里不需要 unknown（刻意不补，不是漏）
--------------------------------------------
§4.8 的 `UNKNOWN` 属于**派发层**：派单发出去了，但不知道对方有没有开始执行。
那一层必须对账，因为没有别的证据能回答「发出去的那条到底跑没跑」。

交付层不同：`deliver()` 是**同步调用**，执行器的返回值就是结果——它返回了，
就说明这次调用结束了；它抛异常，说明调用没正常返回。这里没有「在途」这个概念，
所以没有 unknown 的位置。在这层再造一个对账机制，会与 `effect_log` 的三值 status
打架（unknown 既不算成功也不算失败，T20 没有分支能处理它）。

**真正的缓解在幂等键**：真实执行器必须尊重 `idempotency_key`——同一个 key
不得产生第二个副作用。这样即使「执行了但记成 failed」再重试，也不会重复副作用。
这条是执行器实现的契约，写在 `EffectExecutor` 的 docstring 里。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from .model import EffectRecord, SideEffectDecl

# 一次交付里单条副作用的处置结果（不是 effect_log 的 status）。
STEP_EXECUTED = "executed"
STEP_FAILED = "failed"
STEP_REPLAYED = "replayed"


@dataclass(frozen=True)
class EffectResult:
    """执行器对一条副作用的答复。

    只有两态：`ok` 真 / 假。**故意不引入 unknown**——§4.8 的 UNKNOWN 属于派发层
    （发出去了但不知道对方有没有执行），而交付层是确定性代码的同步调用，
    返回值就是结果。在这里再造一个对账机制会与 `effect_log` 的三值 status 打架。

    `value` 是执行器给的产物/回执（重放时要原样返回首次结果），
    `error` 是失败原因（只进 `DeliveryStep`，因为 `EffectRecord` 没有 detail 字段）。
    """

    ok: bool
    value: Any = None
    error: str = ""


@runtime_checkable
class EffectExecutor(Protocol):
    """执行一条副作用。实现方：测试里的 lambda、真实 Worker 适配器、远端网关。

    契约：**同一 `idempotency_key` 重复调用不得产生第二个副作用**。
    交付层已经用 `effect_log` 挡住了重复，但执行器自己也必须尊重这个键——
    否则「记了但没执行」与「执行了但没记」两条路径都会破坏幂等。
    """

    def __call__(self, effect: SideEffectDecl) -> EffectResult: ...


@dataclass(frozen=True)
class DeliveryStep:
    """本次交付中一条副作用的处置过程（带 value/error，是过程不是账目）。

    `action` 是 executed / failed / replayed 三者之一，与 `EffectRecord.status` 的
    done / failed / replayed 一一对应，但**不要互相替代**：status 进账本，
    action 描述这次调用发生了什么。
    """

    idempotency_key: str
    action: str
    value: Any = None
    error: str = ""


@dataclass(frozen=True)
class DeliveryOutcome:
    """一次交付的结果。

    `records` 是**完整** `effect_log`（旧记录 + 本次新增），可以直接赋给
    `TaskFacts.effect_records` 交给 T20 判定。`steps` 是按输入顺序的本次过程。
    """

    records: tuple[EffectRecord, ...]
    steps: tuple[DeliveryStep, ...]

    @property
    def ok(self) -> bool:
        """本次是否每一条都成功（含因幂等被重放的）。"""
        return all(step.action != STEP_FAILED for step in self.steps)


def _effect_id(effect: SideEffectDecl, action: str) -> str:
    """本条记录的 id（§5.2 DELIVERY_EFFECT 的 `effect_id`）。

    由「副作用的契约 id + 本次处置」组成：同一副作用被重试会产生多条记录，
    `effect_id` 必须区分它们，否则 `APPROVE_SIDE_EFFECT` 定位不到是哪一次。
    不引入随机数或时钟——那会让同输入不同输出，测试不可复现。
    """
    return f"{effect.id}:{action}"


def _require_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} 必须是整数，实际 {type(value).__name__}")
    if value < 0:
        raise ValueError(f"{name} 不能为负，实际 {value}")
    return value


def deliver(
    side_effects: tuple[SideEffectDecl, ...] | list[SideEffectDecl],
    effect_log: tuple[EffectRecord, ...] | list[EffectRecord],
    executor: EffectExecutor,
    *,
    now_ms: int,
) -> DeliveryOutcome:
    """执行副作用并返回新的 `effect_log`。

    按输入顺序逐条处理。`seen_done` 是「已经有 done 记录的 key」集合：
    - 命中 -> 记一条 `replayed`，**不调执行器**（SM-11）
    - 未命中 -> 调执行器；ok 记 `done` 并加入 seen_done，否则记 `failed`

    同一次调用里重复的 key 不需要单独分支：第一条执行后已进 seen_done，
    第二条自然走重放。
    """
    if not callable(executor):
        raise TypeError(f"executor 必须可调用，实际 {type(executor).__name__}")
    stamp = _require_int(now_ms, "now_ms")

    records = list(effect_log or ())
    seen_done = {
        record.idempotency_key for record in records if record.status == "done"
    }
    steps: list[DeliveryStep] = []

    for effect in side_effects or ():
        key = (effect.idempotency_key or "").strip()
        if not key:
            raise ValueError(
                f"副作用 {effect.id!r} 没有 idempotency_key：没有幂等键就没有幂等，"
                f"不能静默按普通执行处理"
            )

        if key in seen_done:
            records.append(EffectRecord(
                effect_id=_effect_id(effect, STEP_REPLAYED),
                idempotency_key=key,
                status="replayed",
                type=effect.type,
                target_class=effect.target_class,
                at=stamp,
            ))
            steps.append(DeliveryStep(idempotency_key=key, action=STEP_REPLAYED))
            continue

        result = executor(effect)
        if not isinstance(result, EffectResult):
            raise TypeError(
                f"执行器必须返回 EffectResult，实际 {type(result).__name__}；"
                f"返回值类型错是调用方的 bug，不记成 failed（那会把执行器写错说成副作用失败）"
            )

        if result.ok:
            records.append(EffectRecord(
                effect_id=_effect_id(effect, STEP_EXECUTED),
                idempotency_key=key,
                status="done",
                type=effect.type,
                target_class=effect.target_class,
                at=stamp,
            ))
            seen_done.add(key)
            steps.append(DeliveryStep(
                idempotency_key=key, action=STEP_EXECUTED, value=result.value))
        else:
            records.append(EffectRecord(
                effect_id=_effect_id(effect, STEP_FAILED),
                idempotency_key=key,
                status="failed",
                type=effect.type,
                target_class=effect.target_class,
                at=stamp,
            ))
            steps.append(DeliveryStep(
                idempotency_key=key, action=STEP_FAILED, error=result.error))

    return DeliveryOutcome(records=tuple(records), steps=tuple(steps))


def pending_effects(
    side_effects: tuple[SideEffectDecl, ...] | list[SideEffectDecl],
    effect_log: tuple[EffectRecord, ...] | list[EffectRecord],
) -> tuple[SideEffectDecl, ...]:
    """还没成功执行过的副作用（给调用方决定要不要重新派交付）。

    判据与 `deliver` 一致：key 已经有 `done` 记录就算完成。
    """
    done = {record.idempotency_key for record in (effect_log or ())
            if record.status == "done"}
    return tuple(effect for effect in (side_effects or ())
                 if (effect.idempotency_key or "").strip() not in done)


def declared_keys(
    side_effects: tuple[SideEffectDecl, ...] | list[SideEffectDecl],
) -> tuple[str, ...]:
    """契约声明的全部幂等键（给 `TaskFacts.declared_effect_keys` 用）。"""
    return tuple((effect.idempotency_key or "").strip() for effect in (side_effects or ()))
