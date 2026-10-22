# `effect_log` 的形状（M1 第 6 步前置，草案）

**为什么要先定它**：规范里 `effect_log` 出现 6 处，**没有一处给出结构**，只有引用。
而 `server/task/machine.py` 的 T20 守卫已经默认了它是一条「key 列表」——
这不是「待定」，是**已经被实现选定了一个形状**，且那个形状不够用。

**本文不改编规范正文**，只回答「一条记录是什么」以及「T20 守卫要怎么改」。
规范 §4.9 / §5.2 的 `effect_log` 描述在授权下来后按本文回填。

---

## 一、现状：两个要求合不起来

| 出处 | 要求 |
|---|---|
| 规范 §4.4 T20 守卫 | 每条 `side_effects[].idempotency_key` 在 `effect_log` 里**恰好一条成功记录** |
| 规范 §5.2 `DELIVERY_EFFECT` 事件 | `effect_id, type, target_class, status, idempotency_key` |
| 实现 `machine.py:276-282` | `effect_records: tuple[str, ...]`，`count = sum(1 for r in records if r == key)` |

**冲突**：只存 key 就丢了 `status`——**失败的那次也被算进去了**；
存全量字段又与 `tuple[str, ...]` 的形状冲突。

所以 T20 守卫现在这句（`machine.py:279`）：

```python
count = sum(1 for record in records if record == key)
if count != 1:
```

**读的是 key 的出现次数，不是「成功记录」的条数。** 规范 `:551` 写的是「恰好一条**成功**记录」，
实现没读 `status`。**这是一个真实偏离**：一旦有失败记录，`count != 1` 会误判。

---

## 二、一条 `effect_log` 记录是什么

定为 **frozen dataclass**（`server/task/model.py` 的 `EffectRecord`），字段与 §5.2 的
`DELIVERY_EFFECT` 载荷对齐：

```python
EFFECT_STATUSES = ("done", "failed", "replayed")   # 闭集

@dataclass(frozen=True)
class EffectRecord:
    idempotency_key: str          # 必填。幂等键，契约层给，同一副作用永远同一键
    status: str                   # 必填。闭集三值之一，构造时校验
    type: str = ""                # 副作用类型，取自 side_effects[].type
    target_class: str = ""        # 本机 / 远端 / 他人
    at: int = 0                   # epoch ms，服务端时钟

    def __post_init__(self):
        if not self.idempotency_key:
            raise ValueError("EffectRecord 缺少 idempotency_key")
        if self.status not in EFFECT_STATUS_SET:
            raise ValueError(f"EffectRecord.status 必须是 {EFFECT_STATUSES} 之一，实际 {self.status!r}")
```

**为什么是 frozen dataclass 而不是 dict**（`3f950f8` 按首席架构意见改，原草案写的是 dict）：

`status` 是**闭集**。用 dict 时 `status="DONE"` 或拼错成 `"succes"` 会**静默**落进
「不是 done」的分支，T20 把它当失败记录、永远拒绝，而没人知道是拼错了——
**这跟 T20 原本那个 bug 是同一种形状：读不到就不报错，只给出错误结论。**

dataclass 在**构造时**炸（`__post_init__` 校验闭集），与本包既有纪律一致：
`failures.py:114/133/145`、`retry.py:149`、`SideEffectDecl.from_mapping`
都是「构造时 raise ValueError，不在守卫里兜」。实测 `status="DONE"` 直接抛错。

**`attempt` 和 `detail` 不加**（首席架构意见，原草案有这两个字段）：
`attempt` 现在没有消费者——T20 不看他，加了就是凭猜测设计；
且它将来的「按 attempt 去重」会和「按 key 去重」打架。
真要追溯重试，`at` + `status` 序列已经够看出来。等第 6 步 `delivery.py`
真实现时，它知道自己需要什么，那时再加。

---

## 三、`status` 三个取值的语义

| 值 | 含义 | 计入 T20 的「成功记录」吗 |
|---|---|---|
| `done` | 这次真的执行了副作用，且成功 | **是** |
| `failed` | 这次尝试了但失败 | 否 |
| `replayed` | 因幂等键命中，**没有再次执行**，直接返回首次结果 | 否 |

**为什么要有 `replayed`**：SM-11 要求「第二次直接返回首次结果，不产生第二个副作用」。
如果重放什么都不记，事后无法区分「没触发过」和「触发了但被幂等挡住」——这两件事在排查时完全不同。
记一条 `replayed` 既有痕迹，又不破坏 T20（它只数 `done`）。

**这一条是我的建议，规范没说，标为待用户确认。** 反对意见若成立（比如「重放不该留痕」），
改成不记即可，T20 守卫不受影响。

---

## 四、T20 守卫要怎么改

**Agent 集成工程师实测确认：这个偏离是双向的，比我初稿写的更严重。**

| 场景 | 当前行为 | 应该是 |
|---|---|---|
| 1 条 `failed` 记录，key 对 | **放行进 `DELIVERED`** | 拒绝（0 条成功记录） |
| 1 条 `done` + 1 条 `failed` | **拒绝**（数到 2） | 放行（恰好 1 条成功） |
| 1 条 `done` | 放行 | 放行 |

**两个方向都错**：失败记录被当成成功，而「成功 + 失败」这种正常重试反而被拒。
所以这不是「少读一个字段」，是**判定方向反了**——按次数数和按成功条数数，
在只有失败记录、和有失败记录混入时给出相反结论。

```python
# 现在（machine.py:276-282）：数 key 出现次数
count = sum(1 for record in records if record == key)

# 改成：数「该 key 且 status == done」的条数
count = sum(
    1 for record in records
    if record.get("idempotency_key") == key and record.get("status") == "done"
)
if count != 1:
    return f"idempotency_key {key!r} 在 effect_log 里有 {count} 条成功记录（应为 1）"
```

**连带影响**：`TaskFacts.effect_records` 的类型从 `tuple[str, ...]` 改为
`tuple[dict[str, Any], ...]`。这是**新包内部的类型**，不涉及协议、不涉及台账 schema。

**这一条测试要同步**：现有断言用的是字符串列表，改类型后会红。
红了是对的——它说明类型变化被测试看见了。**改测试前先确认那条断言保护的是
「T20 数成功记录」还是「effect_records 是字符串」**：前者要保留，后者才是要改的。

---

## 五、`effect_log` 与事件的分工

```
第 6 步：effect_log 落盘（本地 JSONL）
第 8 步：DELIVERY_EFFECT 事件上 WS（有协议门）
```

**事件上线之前，`effect_log` 是唯一真相源。** 事件上线之后，两者语义相同但用途不同：
`effect_log` 是持久账目（重启后还在），事件是实时广播（断了可补齐，但不负责持久）。

**这条必须写明**，否则第 8 步会有人以为「事件发了就不用落盘」。

---

## 六、这份草案不做什么

- **不改规范正文**。授权未定前，团队不替规范确定解释权；本文只记录「缺什么、该怎么补」。
- **不落盘、不改代码**。`server/task/delivery.py` 还没写，本文只给形状。
- **不决定 `replayed` 要不要记**（第三节已标待确认）。
- **不碰协议**。`effect_log` 是 `server/task/` 内部的持久化形状，不是 `PROTOCOL.md` 里
  任何已文档化字段的扩展——这一点与 C10 的 `audit` 不同，那个确实是扩了文档化字段。
