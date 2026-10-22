"""事件流写 JSONL（规范 §7.1，M1 第 4 步）。

职责
----
把一条事件信封**追加**写到 `data/events/<yyyy-mm-dd>.jsonl`，一行一条，与 §5.1 的信封同形。
本模块只负责「怎么写」，**不定义事件语义**——§5.2 已给九类事件的载荷定义。

边界（与 `server/task/` 其余模块的纪律不同，见下）
--------------------------------------------------
**`server/task/` 全包纪律是「零 IO」，本模块是唯一的例外。**
第 4 步的职责就是写文件，没有 IO 就没有事件流。所以：

- 只允许写 `data/events/*.jsonl` 这一个位置，不给调用方传任意路径的入口；
- 仍然**只 import 标准库**（json / os / pathlib / time / uuid），不 import `server/**`；
- 不读文件、不连库、不开 socket、不发 WS。

**不发 WS**：`DELIVERY_EFFECT` 上 WS 是第 8 步，有 `[PROTOCOL_REQUEST]` 门。
本模块写的是**本地 trace**，不是线上事件。

版本与命名（§5 是「提案，未生效」）
-----------------------------------
规范 §5 的章节引言写着「状态：提案，未生效」，九类事件名一律要 `[PROTOCOL_REQUEST]`。
**所以这批事件名将来可能改名。** 每条记录带 `v: 2`（§5.1 的信封版本），
且 `type` 的值是**当前提案名**。没有这个版本号，改名后历史文件分不清
「e10 是旧名还是新名」——`v` 让读的人知道该用哪一版映射表去解释 `type`。

注意：§5.1 明说 `v` 是**信封版本**，不是 schema 版本。schema 演进靠
「未知字段忽略、未知 type 整条忽略」，不靠 `v` 递增。

原子性
------
`open('a')` 追加单行，且**整行只调一次 write()**。理由：

- Python 的 `TextIOWrapper.write()` 在一次调用内把整行交给底层写系统调用；
  对远小于系统缓冲的行（本文未做上限校验，见下），这一次调用是原子的。
- 进程崩在写之前 -> 没有这一行；崩在写之后 -> 这一行完整。**不存在写一半的行。**
- 不需要文件锁：单进程单写者，没有并发写同一文件。

`write()` 的返回值必须等于整行字节数，否则抛 `EventLogError`——「写了一半却返回成功」
是最难查的一种，宁可炸。

**不做的**：不校验行大小上限（§5.1 没给）、不轮转文件（按日期分文件已够）、
不写 reader（第 7 步真有需求时再写，现在写就是没有消费者）。
"""
from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# 信封版本（§5.1）。改动它就是改协议，必须走 [PROTOCOL_REQUEST]。
ENVELOPE_VERSION = 2

# 默认落点（§7.1：data/events/<yyyy-mm-dd>.jsonl）。
# 相对路径由调用方给基准目录，本模块不猜仓库结构。
DEFAULT_SUBDIR = "events"


class EventLogError(RuntimeError):
    """写事件失败。**不吞**——事件流是 trace，静默丢行会让回放对不上。"""


@dataclass(frozen=True)
class EventEnvelope:
    """一条事件的信封（§5.1）。

    可选字段为 None 时**不出现在 JSON 里**（而不是写 null）——§5.1 的
    `task_id?` / `step_id?` 是可选，不是可空。
    """

    type: str
    payload: Any
    seq: int
    ts: int
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    task_id: str = ""
    step_id: str = ""
    session_id: str = ""
    turn_id: str = ""

    def to_mapping(self) -> dict[str, Any]:
        """转成落盘用的 dict，键序与 §5.1 的信封一致（便于人读）。"""
        if not self.type:
            raise EventLogError("事件缺少 type")
        data: dict[str, Any] = {
            "v": ENVELOPE_VERSION,
            "id": self.id,
            "seq": self.seq,
            "ts": self.ts,
            "type": self.type,
        }
        for key in ("task_id", "step_id", "session_id", "turn_id"):
            value = getattr(self, key)
            if value:
                data[key] = value
        data["payload"] = self.payload
        return data


def event_path(base_dir: str | os.PathLike[str], *, now: float | None = None) -> Path:
    """当天的 JSONL 路径：`<base_dir>/events/<yyyy-mm-dd>.jsonl`。

    `now` 用 Unix 秒（与台账一致），只用来分文件；事件本身的 `ts` 是 epoch ms（§5.1）。
    显式传 `now` 是为了测试可复现——本模块不读时钟以外的状态。
    """
    stamp = time.time() if now is None else now
    day = time.strftime("%Y-%m-%d", time.localtime(stamp))
    return Path(base_dir) / DEFAULT_SUBDIR / f"{day}.jsonl"


def append_event(base_dir: str | os.PathLike[str], envelope: EventEnvelope,
                 *, now: float | None = None) -> Path:
    """追加一条事件，返回写入的文件路径。

    **整行只调一次 write()**（见模块 docstring 的原子性说明）。
    写入字节数不符即抛 `EventLogError`——不把「写了一半」当成功。
    """
    data = envelope.to_mapping()
    line = json.dumps(data, ensure_ascii=False, separators=(",", ":")) + "\n"
    payload = line.encode("utf-8")

    path = event_path(base_dir, now=now)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # 用二进制追加：避免 TextIOWrapper 的换行转换与编码两次处理，
        # 字节数与 write 返回值可以逐字节核对。
        with path.open("ab") as handle:
            written = handle.write(payload)
    except OSError as exc:
        raise EventLogError(f"写事件失败：{path}：{exc}") from exc

    if written != len(payload):
        raise EventLogError(
            f"事件只写了一部分：{path} 期望 {len(payload)} 字节，实际 {written}"
        )
    return path


def next_seq(existing: int | None = None) -> int:
    """连接级 `seq` 的下一个值（§5.1 取舍 1：连接级单调，不是全局）。

    由调用方持有当前值：本模块不维护全局状态（那会变成第二个真相源）。
    """
    return 1 if existing is None else int(existing) + 1
