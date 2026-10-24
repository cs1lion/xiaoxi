# -*- coding: utf-8 -*-
"""§25 事件协议 v2 的线上广播包。

broadcast 层住在这里而不在 `server/contract/`——contract 目录冻结为纯契约层
（lint/gate/risk，一行 publish/subscribe/handle 都不许有，
`scripts/test_contract.py` 的边界守卫锁着这条线）。广播是运行时实现，
属于业务侧：它 import contract 的常量与发射层（允许方向），
反方向（contract import 业务）仍然不存在。

公开面从 `v2.py` 原样再导出，调用方（app.py / dispatcher / memory manager）
只认包名。
"""
from .v2 import CANCEL_REASONS, Reply, Sink
from .v2 import (CommissionEventBridge, RequestLedger, V2BroadcastHub,
                 V2CommandActions, validate_command)
from .v2 import (publish_memory_update, publish_presence,
                 publish_tool_execution, v2_bridge, v2_hub)

__all__ = [
    "CANCEL_REASONS", "Reply", "Sink",
    "CommissionEventBridge", "RequestLedger", "V2BroadcastHub",
    "V2CommandActions", "validate_command",
    "publish_memory_update", "publish_presence", "publish_tool_execution",
    "v2_bridge", "v2_hub",
]
