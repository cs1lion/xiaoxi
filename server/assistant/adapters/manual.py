"""manual 适配器：只组密件、不执行。

花名册里没写 `adapter` 或写成 `manual` 的 Agent 走这一支。它不假装自己在干活——
只吐一条观测用的 log，没有 artifact，于是派单方会把督办转成 BLOCKED 并向老板说明
"这个 Agent 还没接执行体"。宁可卡住说清楚，也不要伪造一次执行。
"""
from __future__ import annotations

from typing import Any, AsyncIterator

from .base import LOG, BaseWorkerAdapter, make_event
from .registry import register_adapter


@register_adapter("manual")
class ManualAdapter(BaseWorkerAdapter):
    adapter_type = "manual"

    async def prepare_context(self, commission_id: str,
                              briefing_packet: dict[str, Any]) -> dict[str, Any]:
        agent_name = self.config.get("name") or self.config.get("id") or "未命名 Agent"
        return {
            "commission_id": commission_id,
            "agent_id": str(self.config.get("id") or ""),
            "agent_name": str(agent_name),
            "objective": str((briefing_packet or {}).get("objective") or ""),
            "deliverable": str((briefing_packet or {}).get("deliverable") or ""),
            "constraints": list((briefing_packet or {}).get("constraints") or []),
            "env": {},
            "note": "该 Agent 未配置执行体，仅完成派单登记。",
        }

    async def execute(self, task_spec: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        agent_name = self.config.get("name") or self.config.get("id") or "未命名 Agent"
        yield make_event(LOG, step_idx=0,
                         text=f"{agent_name} 没有配置执行体，本单不会自动执行。")

    async def cancel(self) -> None:
        return None


__all__ = ["ManualAdapter"]
