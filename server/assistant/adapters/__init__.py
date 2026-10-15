"""Worker 适配器包：统一契约 + 注册表 + 四种内置实现。

import 本包即完成全部内置适配器登记（副作用仅限注册表，不起进程、不连网络）。
调度核心只应使用这里的 `create_adapter_for`，不要直接 import 具体适配器类——
那样就又回到"每加一种 Agent 改一次调度核心"的老路了。
"""
from __future__ import annotations

from .base import (EVENT_TYPES, TERMINAL_EVENTS, AdapterError, BaseWorkerAdapter,
                   UnknownAdapterError, artifact_event, make_event, token_event)
from .registry import (adapter_types, create_adapter, create_adapter_for,
                       get_adapter_class, register_adapter, reset_registry,
                       unregister_adapter)

# 登记内置实现。顺序即 adapter_types() 的顺序。
from . import manual as _manual  # noqa: F401  (manual 是缺省执行体)
from . import cli_subproc as _cli_subproc  # noqa: F401
from . import http_api as _http_api  # noqa: F401
from . import mcp as _mcp  # noqa: F401

__all__ = ["EVENT_TYPES", "TERMINAL_EVENTS", "AdapterError", "BaseWorkerAdapter",
           "UnknownAdapterError", "adapter_types", "artifact_event", "create_adapter",
           "create_adapter_for", "get_adapter_class", "make_event", "register_adapter",
           "reset_registry", "token_event", "unregister_adapter"]
