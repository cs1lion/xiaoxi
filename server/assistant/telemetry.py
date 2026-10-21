"""轻量观测网关：本地透明 API 代理 + 子进程事件流 + 鬼打墙熔断。

契约见 docs/PROTOCOL.md §20。三条底线写在这里，因为它们最容易被"顺手优化"掉：

1. **只记录发生了什么，不记录想了什么。** span 里没有 prompt/response 正文，
   只有 token 数、耗时、状态码和截断后的错误片段。
2. **落库失败不能带崩调用。** 任何一条 span 写不进去，就当这次不计入战况，
   Worker 的响应照样原样返回。
3. **熔断只认形状，不猜语义。** 同一步骤签名连续重复即熔断；不做相似度模型，
   所以不会把"合理的重复步骤"误判成鬼打墙。
"""
from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
import time
from collections import deque
from pathlib import Path
from typing import Any, Callable

SPAN_FIELDS: tuple[str, ...] = (
    "commission_id", "agent_id", "step_idx", "model", "prompt_tokens",
    "completion_tokens", "ttft_ms", "duration_ms", "status_code", "error_snippet",
)
MAX_ERROR_SNIPPET = 200
MAX_SPANS_RETURNED = 200
MAX_TRACKED_COMMISSIONS = 64

_SENSITIVE = re.compile(
    r"api[_-]?key|access[_-]?token|refresh[_-]?token|password|passwd|secret|"
    r"credential|authorization|cookie|bearer\s+\S+|sk-[A-Za-z0-9]{10,}",
    re.I,
)

# 步骤签名里只压掉真正的措辞噪音：空白、大小写、中英标点。字不同就是不同输出。
_NOISE = re.compile(r"[\s。，、；：！？…—－·「」『』（）()\[\]{}<>\"'`~@#$%^&*_+=|\\/]+")


def clip_error(value: Any) -> str:
    """Bound an error snippet and strip credential-shaped text before it is stored."""
    if value is None:
        return ""
    text = value if isinstance(value, str) else str(value)
    text = _SENSITIVE.sub("[REDACTED]", text)
    text = " ".join(text.split())
    return text[:MAX_ERROR_SNIPPET]


def _count(value: Any, name: str) -> int:
    if value is None or value == "":
        return 0
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a non-negative number")
    number = int(value)
    return number if number > 0 else 0


def _text(value: Any, name: str, maximum: int = 200) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{name} must be text")
    return value[:maximum]


def normalize_signature(payload: Any) -> str:
    """Stable signature for one worker step; hashes the call, not its prose.

    The action name, the full argument set and the normalized output all count.
    Only prose noise (whitespace, casing, punctuation) is collapsed, so a genuinely
    reworded or differently-parametered step is not a false positive -- a breaker
    that fires on "read three different notes" would interrupt the boss for nothing,
    which costs far more than the tokens it saves.
    """
    if not isinstance(payload, dict):
        payload = {"text": payload}
    action = str(payload.get("action") or payload.get("tool") or "")[:80]
    arguments = payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {}
    try:
        encoded_arguments = json.dumps(arguments, ensure_ascii=False, sort_keys=True,
                                       separators=(",", ":"), default=str)[:600]
    except (TypeError, ValueError):
        encoded_arguments = "|".join(sorted(str(key) for key in arguments))[:600]
    output = payload.get("output") or payload.get("text") or ""
    if not isinstance(output, str):
        output = json.dumps(output, ensure_ascii=False, sort_keys=True, default=str)
    # 只压掉真正的措辞噪音：空白、大小写、标点。字不同就是不同输出。
    output = _NOISE.sub("", output).lower()[:400]
    seed = json.dumps({"action": action, "arguments": encoded_arguments, "output": output},
                      ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]


def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), 1) if values else None


class TelemetryStore:
    """`agent_telemetry_spans` 的唯一写入方；字段固定，见 PROTOCOL §20。"""

    def __init__(self, path: str | Path = "data/telemetry.db", clock: Callable[[], float] = time.time):
        self.path = str(path)
        self.clock = clock
        self._lock = threading.RLock()
        self._closed = False
        if self.path != ":memory:":
            Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path, timeout=30, check_same_thread=False,
                                   isolation_level=None)
        self._db.row_factory = sqlite3.Row
        self._db.executescript("""
        CREATE TABLE IF NOT EXISTS agent_telemetry_spans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            commission_id TEXT NOT NULL,
            agent_id TEXT NOT NULL,
            step_idx INTEGER NOT NULL DEFAULT 0,
            model TEXT NOT NULL DEFAULT '',
            prompt_tokens INTEGER NOT NULL DEFAULT 0,
            completion_tokens INTEGER NOT NULL DEFAULT 0,
            ttft_ms INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            status_code INTEGER NOT NULL DEFAULT 0,
            error_snippet TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL);
        CREATE INDEX IF NOT EXISTS telemetry_commission ON agent_telemetry_spans(commission_id, step_idx);
        CREATE INDEX IF NOT EXISTS telemetry_agent ON agent_telemetry_spans(agent_id, created_at);
        CREATE INDEX IF NOT EXISTS telemetry_created ON agent_telemetry_spans(created_at);
        """)

    def record_span(self, *, commission_id: str, agent_id: str, step_idx: int = 0, model: str = "",
                    prompt_tokens: int = 0, completion_tokens: int = 0, ttft_ms: int = 0,
                    duration_ms: int = 0, status_code: int = 0, error_snippet: str = "") -> dict[str, Any]:
        span = {
            "commission_id": _text(commission_id, "commission_id", 100),
            "agent_id": _text(agent_id, "agent_id", 100),
            "step_idx": _count(step_idx, "step_idx"),
            "model": _text(model, "model", 120),
            "prompt_tokens": _count(prompt_tokens, "prompt_tokens"),
            "completion_tokens": _count(completion_tokens, "completion_tokens"),
            "ttft_ms": _count(ttft_ms, "ttft_ms"),
            "duration_ms": _count(duration_ms, "duration_ms"),
            "status_code": _count(status_code, "status_code"),
            "error_snippet": clip_error(error_snippet),
        }
        with self._lock:
            if self._closed:
                raise ValueError("telemetry store is closed")
            cursor = self._db.execute(
                "INSERT INTO agent_telemetry_spans (commission_id, agent_id, step_idx, model,"
                " prompt_tokens, completion_tokens, ttft_ms, duration_ms, status_code,"
                " error_snippet, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (span["commission_id"], span["agent_id"], span["step_idx"], span["model"],
                 span["prompt_tokens"], span["completion_tokens"], span["ttft_ms"],
                 span["duration_ms"], span["status_code"], span["error_snippet"],
                 self.clock()))
            span["id"] = int(cursor.lastrowid)
            span["created_at"] = self.clock()
        return span

    def spans(self, *, commission_id: str | None = None, agent_id: str | None = None,
              since: float | None = None, limit: int = MAX_SPANS_RETURNED) -> list[dict[str, Any]]:
        clauses, params = [], []
        if commission_id:
            clauses.append("commission_id=?")
            params.append(commission_id)
        if agent_id:
            clauses.append("agent_id=?")
            params.append(agent_id)
        if since is not None:
            clauses.append("created_at>=?")
            params.append(float(since))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(max(1, min(int(limit), MAX_SPANS_RETURNED)))
        with self._lock:
            rows = self._db.execute(
                f"SELECT * FROM agent_telemetry_spans {where} ORDER BY id DESC LIMIT ?",
                params).fetchall()
        return [dict(row) for row in rows]

    def commission_usage(self, commission_id: str) -> dict[str, Any]:
        """Real token/latency totals for one commission; L1 budget checks read this.

        ``prompt_tokens`` is the per-commission SUM (cost), while
        ``max_prompt_tokens_per_call`` is the largest single call (quality).
        Retries must not turn a healthy per-call context into a "bloated" one.
        """
        with self._lock:
            row = self._db.execute(
                "SELECT COUNT(*) AS calls, COALESCE(SUM(prompt_tokens),0) AS prompt_tokens,"
                " COALESCE(SUM(completion_tokens),0) AS completion_tokens,"
                " COALESCE(SUM(duration_ms),0) AS duration_ms,"
                " COALESCE(MAX(prompt_tokens),0) AS max_prompt_tokens_per_call,"
                " COALESCE(MAX(ttft_ms),0) AS max_ttft_ms FROM agent_telemetry_spans"
                " WHERE commission_id=?", (commission_id,)).fetchone()
        total = int(row["prompt_tokens"]) + int(row["completion_tokens"])
        return {"calls": int(row["calls"]), "prompt_tokens": int(row["prompt_tokens"]),
                "completion_tokens": int(row["completion_tokens"]), "total_tokens": total,
                "max_prompt_tokens_per_call": int(row["max_prompt_tokens_per_call"]),
                "duration_ms": int(row["duration_ms"]), "max_ttft_ms": int(row["max_ttft_ms"])}

    def summary(self, *, since: float | None = None, now: float | None = None) -> dict[str, Any]:
        """TelemetrySummary (PROTOCOL §20). Empty is reported as empty, never as healthy."""
        now = self.clock() if now is None else float(now)
        params: list[Any] = []
        where = ""
        if since is not None:
            where, params = "WHERE created_at>=?", [float(since)]
        with self._lock:
            rows = self._db.execute(
                f"SELECT agent_id, prompt_tokens, completion_tokens, ttft_ms, status_code"
                f" FROM agent_telemetry_spans {where} ORDER BY id", params).fetchall()
        calls = len(rows)
        succeeded = sum(1 for row in rows if 200 <= int(row["status_code"]) < 400 or int(row["status_code"]) == 0)
        prompt_tokens = sum(int(row["prompt_tokens"]) for row in rows)
        completion_tokens = sum(int(row["completion_tokens"]) for row in rows)
        ttfts = [float(row["ttft_ms"]) for row in rows if int(row["ttft_ms"]) > 0]
        grouped: dict[str, dict[str, Any]] = {}
        for row in rows:
            agent = row["agent_id"] or "(unattributed)"
            bucket = grouped.setdefault(agent, {"agent_id": agent, "calls": 0, "succeeded": 0,
                                                "prompt_tokens": 0, "completion_tokens": 0,
                                                "_ttft": []})
            bucket["calls"] += 1
            if 200 <= int(row["status_code"]) < 400 or int(row["status_code"]) == 0:
                bucket["succeeded"] += 1
            bucket["prompt_tokens"] += int(row["prompt_tokens"])
            bucket["completion_tokens"] += int(row["completion_tokens"])
            if int(row["ttft_ms"]) > 0:
                bucket["_ttft"].append(float(row["ttft_ms"]))
        by_agent = []
        for bucket in grouped.values():
            ttft = bucket.pop("_ttft")
            bucket["total_tokens"] = bucket.pop("prompt_tokens") + bucket.pop("completion_tokens")
            bucket["avg_ttft_ms"] = _mean(ttft)
            bucket["success_rate"] = round(bucket["succeeded"] / bucket["calls"], 3) if bucket["calls"] else None
            by_agent.append(bucket)
        token_ranking = sorted(
            ({"agent_id": item["agent_id"], "total_tokens": item["total_tokens"]} for item in by_agent),
            key=lambda item: item["total_tokens"], reverse=True)
        return {
            "window_seconds": int(now - float(since)) if since is not None else None,
            "calls": calls,
            "succeeded": succeeded,
            "failed": calls - succeeded,
            "success_rate": round(succeeded / calls, 3) if calls else None,
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
            "avg_ttft_ms": _mean(ttfts),
            "max_ttft_ms": max(ttfts) if ttfts else None,
            "by_agent": sorted(by_agent, key=lambda item: item["calls"], reverse=True),
            "token_ranking": token_ranking,
        }

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._db.close()
                self._closed = True

    def __enter__(self) -> "TelemetryStore":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()


class SpinningGuard:
    """鬼打墙熔断：同一 commission 内同一步骤签名连续重复 max_identical 次即跳闸。

    只认归一化签名，不做语义相似度——漏熔一次只是多烧几个 token，
    误熔一次会让总管无缘无故打断老板，后者贵得多。
    """

    def __init__(self, *, max_identical: int = 3, max_tracked: int = MAX_TRACKED_COMMISSIONS):
        if type(max_identical) is not int or max_identical < 2:
            raise ValueError("max_identical must be an integer >= 2")
        self.max_identical = max_identical
        self._recent: dict[str, deque[str]] = {}
        self._alerts: list[dict[str, Any]] = []
        self._tracked = max(4, int(max_tracked))

    def observe(self, commission_id: str, signature: str, *, agent_id: str = "",
                max_identical: int | None = None, channel: str = "") -> dict[str, Any]:
        """记一次观测，返回是否跳闸。

        `channel` 把不同来源的签名流分开计数（Worker 步骤 vs 上游 API 调用）：
        混在同一个窗口里会互相顶掉重复计数，两种熔断都会失灵。告警里记的仍然
        是真实 commission_id。
        `max_identical` 让花名册里这一单的阈值生效；缺省用实例阈值。阈值变化时
        重新开窗，不背着上一个配置的计数。
        """
        limit = self.max_identical if max_identical is None else int(max_identical)
        if limit < 2:
            raise ValueError("max_identical must be an integer >= 2")
        key = f"{channel}:{commission_id}" if channel else commission_id
        history = self._recent.get(key)
        if history is None or history.maxlen != limit:
            history = deque(maxlen=limit)
            self._recent[key] = history
        if len(self._recent) > self._tracked:
            for stale in list(self._recent)[: len(self._recent) - self._tracked]:
                self._recent.pop(stale, None)
        history.append(signature)
        repeats = 0
        if len(history) == limit and len(set(history)) == 1:
            repeats = limit
        tripped = repeats == limit
        if tripped:
            alert = {"commission_id": commission_id, "agent_id": agent_id,
                     "repeats": repeats, "signature": signature}
            if not any(item["commission_id"] == commission_id and item["signature"] == signature
                       for item in self._alerts):
                self._alerts.append(alert)
        return {"tripped": tripped, "repeats": repeats, "signature": signature}

    def alerts(self) -> list[dict[str, Any]]:
        return [dict(item) for item in self._alerts]

    def reset(self, commission_id: str) -> None:
        """清空这一单的计数窗口（含各 channel）。告警历史不动——它要留在战况里。"""
        for key in [item for item in self._recent
                    if item == commission_id or item.endswith(f":{commission_id}")]:
            self._recent.pop(key, None)


class TelemetryGateway:
    """HTTP Shim + Dual-Shim 子进程事件流，两条链路落同一张 span 表。"""

    def __init__(self, store: TelemetryStore | None = None, *, guard: SpinningGuard | None = None,
                 clock: Callable[[], float] = time.time, path: str | Path = "data/telemetry.db"):
        self.store = store if store is not None else TelemetryStore(path, clock=clock)
        self.guard = guard if guard is not None else SpinningGuard()
        self.clock = clock

    async def proxy(self, payload: dict[str, Any], transport: Callable[[dict[str, Any]], Any]) -> dict[str, Any]:
        """Forward one worker call, record its span, and report a spinning trip.

        `transport` is an async callable returning {"status_code", "body", "ttft_ms",
        "usage"} (or a (status_code, body, ttft_ms) tuple). The request body is
        forwarded untouched; this gateway only observes.
        """
        if not isinstance(payload, dict):
            raise ValueError("proxy payload must be an object")
        commission_id = _text(payload.get("commission_id"), "commission_id", 100)
        agent_id = _text(payload.get("agent_id"), "agent_id", 100)
        step_idx = _count(payload.get("step_idx"), "step_idx")
        model = _text(payload.get("model"), "model", 120)
        started = self.clock()
        status_code, body, ttft_ms, usage, error = 0, None, 0, {}, ""
        try:
            outcome = transport(payload)
            if hasattr(outcome, "__await__"):
                outcome = await outcome
            if isinstance(outcome, tuple):
                status_code = _count(outcome[0] if len(outcome) > 0 else 0, "status_code")
                body = outcome[1] if len(outcome) > 1 else None
                ttft_ms = _count(outcome[2] if len(outcome) > 2 else 0, "ttft_ms")
                usage = outcome[3] if len(outcome) > 3 and isinstance(outcome[3], dict) else {}
            elif isinstance(outcome, dict):
                status_code = _count(outcome.get("status_code"), "status_code")
                body = outcome.get("body")
                ttft_ms = _count(outcome.get("ttft_ms"), "ttft_ms")
                usage = outcome.get("usage") if isinstance(outcome.get("usage"), dict) else {}
                error = clip_error(outcome.get("error"))
            else:
                raise ValueError("transport_outcome_invalid")
        except Exception as exc:  # A failing worker call is data, not a crash.
            status_code, error = 0, clip_error(f"{type(exc).__name__}: {exc}")
        duration_ms = _count((self.clock() - started) * 1000, "duration_ms")
        span = self._safe_record(commission_id=commission_id, agent_id=agent_id, step_idx=step_idx,
                                 model=model, prompt_tokens=_count(usage.get("prompt_tokens"), "prompt_tokens"),
                                 completion_tokens=_count(usage.get("completion_tokens"), "completion_tokens"),
                                 ttft_ms=ttft_ms, duration_ms=duration_ms, status_code=status_code,
                                 error_snippet=error or clip_error(payload.get("error")))
        signature = payload.get("signature")
        if not isinstance(signature, str) or not signature:
            signature = normalize_signature({"action": payload.get("path"), "arguments": payload.get("body"),
                                             "output": body})
        spinning = self.guard.observe(commission_id, signature, agent_id=agent_id)
        return {"status_code": status_code, "body": body, "span": span, "spinning": spinning,
                "usage": {"prompt_tokens": span["prompt_tokens"],
                          "completion_tokens": span["completion_tokens"]}}

    def record_event(self, event: dict[str, Any]) -> dict[str, Any]:
        """Dual-Shim: normalize one subprocess stdout event into the same span shape."""
        if not isinstance(event, dict):
            raise ValueError("event must be an object")
        span = self._safe_record(
            commission_id=_text(event.get("commission_id"), "commission_id", 100),
            agent_id=_text(event.get("agent_id"), "agent_id", 100),
            step_idx=_count(event.get("step_idx"), "step_idx"),
            model=_text(event.get("model"), "model", 120),
            prompt_tokens=_count(event.get("prompt_tokens"), "prompt_tokens"),
            completion_tokens=_count(event.get("completion_tokens"), "completion_tokens"),
            ttft_ms=_count(event.get("ttft_ms"), "ttft_ms"),
            duration_ms=_count(event.get("duration_ms"), "duration_ms"),
            status_code=_count(event.get("status_code"), "status_code"),
            error_snippet=clip_error(event.get("error")))
        # Token events are accounting, not steps. Their signature has no
        # action/arguments/output, so a multi-step worker's 3rd usage record
        # hashes identically to the first two and falsely trips the spinning
        # guard. Step repetition is judged from progress events; API-call
        # repetition is judged by TelemetryGateway.proxy.
        spinning = {"tripped": False, "repeats": 0, "signature": ""}
        return {"span": span, "spinning": spinning}

    def _safe_record(self, **fields: Any) -> dict[str, Any]:
        """A span that cannot be stored is dropped from the war report, never fatal."""
        try:
            return self.store.record_span(**fields)
        except Exception:
            return {name: fields.get(name, 0 if name not in ("commission_id", "agent_id", "model",
                                                             "error_snippet") else "")
                    for name in SPAN_FIELDS} | {"id": None, "recorded": False}

    def summary(self, *, since: float | None = None) -> dict[str, Any]:
        result = self.store.summary(since=since)
        alerts = self.guard.alerts()
        if alerts:
            result["spinning_alerts"] = alerts
        else:
            result["spinning_alerts"] = []
        return result

    def close(self) -> None:
        self.store.close()


def http_transport(base_url: str, *, api_key: str = "", timeout: float = 30.0):
    """Real upstream transport for /internal/telemetry_proxy (not used by tests)."""
    import httpx

    async def forward(payload: dict[str, Any]) -> dict[str, Any]:
        path = str(payload.get("path") or "")
        if not path.startswith("/"):
            raise ValueError("proxy path must be absolute")
        headers = {"content-type": "application/json"}
        if api_key:
            headers["authorization"] = f"Bearer {api_key}"
        started = time.time()
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.request(str(payload.get("method") or "POST").upper(),
                                            base_url.rstrip("/") + path, json=payload.get("body"),
                                            headers=headers)
        usage = {}
        try:
            data = response.json()
            usage = data.get("usage") if isinstance(data.get("usage"), dict) else {}
        except Exception:
            data = None
        return {"status_code": response.status_code, "body": data if data is not None else response.text,
                "ttft_ms": int((time.time() - started) * 1000), "usage": usage}

    return forward


__all__ = ["SPAN_FIELDS", "SpinningGuard", "TelemetryGateway", "TelemetryStore",
           "clip_error", "http_transport", "normalize_signature"]
