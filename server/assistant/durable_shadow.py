"""Shadow 模式：sidecar 照常跑完整执行，结果只落日志，不提交初审。

设计依据 docs/PI_DURABLE_MIGRATION.md §5 Phase 1。用途是拿两套事件流做对照
（步数、token、耗时、产物），所以这里刻意不碰台账：跑完只写一行行 JSONL。
任何失败都只记日志，绝不影响 Python 路径。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

DEFAULT_TIMEOUT = 600.0


class DurableShadow:
    def __init__(self, *, endpoint: str, token: str, log_path: str | Path,
                 timeout: float = DEFAULT_TIMEOUT):
        self.endpoint = endpoint.rstrip("/")
        self.token = token
        self.log_path = Path(log_path)
        self.timeout = float(timeout)

    def _headers(self) -> dict[str, str]:
        return {"content-type": "application/json",
                "authorization": f"Bearer {self.token}"}

    def _log(self, row: dict[str, Any]) -> None:
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        with self.log_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    async def run(self, commission: dict[str, Any]) -> None:
        """跑一遍影子，只写日志。任何异常都吞掉：影子不该影响正主。"""
        commission_id = str(commission.get("id") or "")
        if not commission_id:
            return
        payload = {
            "commission_id": commission_id,
            "agent_id": str(commission.get("agent_id") or ""),
            "packet": {"objective": commission.get("brief") or commission.get("title") or ""},
        }
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                created = await client.post(f"{self.endpoint}/v1/runs",
                                            headers=self._headers(), json=payload)
                if created.status_code >= 400:
                    self._log({"commission_id": commission_id, "error": created.status_code})
                    return
                task_id = int(created.json().get("task_id") or 0)
                async with client.stream("GET", f"{self.endpoint}/v1/events",
                                         params={"task_id": task_id},
                                         headers=self._headers()) as response:
                    if response.status_code >= 400:
                        self._log({"commission_id": commission_id,
                                   "error": f"events:{response.status_code}"})
                        return
                    async for line in response.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw:
                            continue
                        try:
                            event = json.loads(raw)
                        except ValueError:
                            continue
                        self._log({"commission_id": commission_id, "event": event})
        except Exception as exc:
            self._log({"commission_id": commission_id,
                       "error": f"{type(exc).__name__}: {exc}"[:240]})


__all__ = ["DurableShadow"]
