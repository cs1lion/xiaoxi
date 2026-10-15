"""Worker 适配器统一契约：新增一种干活 Agent 只需继承 + 登记两步。

调度核心（dispatcher.py）只认花名册里的 `adapter:` 字符串，通过注册表取类。
这里刻意不出现任何 `if agent_type == ...`——那种分支每加一种 Agent 就要改一次
调度核心，正是"零侵入新增"要禁掉的东西。

三个异步方法的边界：

- `prepare_context` 只组装"发给这个 Agent 的初始化上下文/环境变量"，
  不启动任何进程、不发任何请求。凭据只能来自调用方注入的 config，不得落盘。
- `execute` 是异步生成器，只吐标准事件（见 EVENT_TYPES），不自己改状态机。
- `cancel` 必须幂等且不抛异常：鬼打墙熔断时它可能被调用多次。
"""
from __future__ import annotations

import abc
from typing import Any, AsyncIterator

PROGRESS = "progress"   # 步骤进展
TOKEN = "token"         # 一次模型调用的 token 用量
ARTIFACT = "artifact"   # 最终产出，交给初审室
LOG = "log"             # 仅供观测的原始行
ERROR = "error"         # 失败

EVENT_TYPES = (PROGRESS, TOKEN, ARTIFACT, LOG, ERROR)
TERMINAL_EVENTS = (ARTIFACT, ERROR)


def make_event(event_type: str, **fields: Any) -> dict[str, Any]:
    """构造一个标准事件。未知类型直接拒绝，避免静默吞掉一种新事件。"""
    if event_type not in EVENT_TYPES:
        raise ValueError(f"unknown worker event type: {event_type}")
    return {"type": event_type, **fields}


def token_event(*, model: str = "", prompt_tokens: int = 0, completion_tokens: int = 0,
                ttft_ms: int = 0, duration_ms: int = 0, status_code: int = 0,
                error: str = "", step_idx: int = 0) -> dict[str, Any]:
    """一次模型调用的用量。字段与 agent_telemetry_spans 对齐，便于直接落库。"""
    return make_event(TOKEN, model=model, prompt_tokens=prompt_tokens,
                      completion_tokens=completion_tokens, ttft_ms=ttft_ms,
                      duration_ms=duration_ms, status_code=status_code,
                      error=error, step_idx=step_idx)


def artifact_event(artifact: dict[str, Any]) -> dict[str, Any]:
    """最终产出。`artifact` 必须带 summary / artifacts，否则初审室无从下手。"""
    if not isinstance(artifact, dict):
        raise ValueError("artifact must be an object")
    return make_event(ARTIFACT, artifact=artifact)


class AdapterError(RuntimeError):
    """适配器自身失败（配置缺失、进程起不来、上游不可达）。"""


class UnknownAdapterError(ValueError):
    """花名册写了一个没登记过的 adapter 类型。"""


class BaseWorkerAdapter(abc.ABC):
    """所有干活 Agent 的统一接口。

    子类通过 `adapter_type` 声明自己的类型名，`__init__(config)` 收到的是花名册
    里该 Agent 的整条配置（id/name/adapter/executable/args/endpoint/...），
    因此新增 Agent 类型时不需要改这里，也不需要改调度核心。
    """

    adapter_type: str = ""

    def __init__(self, config: dict[str, Any] | None = None):
        if config is not None and not isinstance(config, dict):
            raise ValueError("adapter config must be an object")
        self.config: dict[str, Any] = dict(config or {})

    @abc.abstractmethod
    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        """组装初始化上下文/环境变量。纯数据，零副作用。"""

    @abc.abstractmethod
    def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        """异步执行任务，以标准事件流向外吐进展、用量与最终产出。"""

    @abc.abstractmethod
    async def cancel(self) -> None:
        """安全中断与子进程回收。幂等，不抛异常。"""

    def describe(self) -> dict[str, Any]:
        """观测用自描述：只暴露类型与配置键名，不暴露配置值（可能含端点/密钥）。"""
        return {"adapter": self.adapter_type, "config_keys": sorted(self.config)}

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"<{type(self).__name__} {self.adapter_type}>"


__all__ = ["EVENT_TYPES", "TERMINAL_EVENTS", "AdapterError", "BaseWorkerAdapter",
           "UnknownAdapterError", "artifact_event", "make_event", "token_event"]
