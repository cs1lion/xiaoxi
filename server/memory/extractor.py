from __future__ import annotations

import re
import time
from typing import Any

from ..config import memory_cfg
from ..llm import _loose_json, llm

KIND_MAP = {"fact": "fact", "episode": "episode", "todo": "todo", "self_state": "self_state"}

EXTRACT_SYSTEM = """你是一个记忆抽取器。从一段对话里抽取**值得长期记住**的信息。

规则：
1. 只记录明确出现的信息。没有的信息不要编。
2. 每条都要判断确定性来源：
   - stated：用户明确说出来的事实/偏好/计划
   - inferred：你从上下文推测出来的（例如"他最近好像很累"）
   - uncertain：含糊、可能过时、你自己都没把握的
3. key 是这条记忆的稳定标签，用来做去重和覆盖，必须是"主题:细节"形式的小写短语，中文即可。
   例如 "游戏:喜欢cs2"、"计划:周五面试"、"称呼:叫我老王"。
   同一主题的新值应当复用同一个 key，这样旧值才会被覆盖而不是堆积。
4. value 用一句完整的中文陈述句，不要带引号和颜文字。
5. 不记录：一次性情绪、时间地点寒暄、系统元信息、模型自己产生的客套话。
6. todo 只记录用户明确提到要做/想聊/约定的事。
7. 最多每类 3 条，宁缺毋滥。没有就返回空数组。

输出 JSON，结构严格如下：
{
  "memories": [
    {"kind": "fact|episode|todo|self_state",
     "subject": "user|self",
     "key": "主题:细节",
     "value": "一句中文陈述句",
     "source": "stated|inferred|uncertain",
     "importance": 0.0-1.0}
  ]
}"""

_FILLER = re.compile(r"^(好的|嗯+|哦+|哈哈+|收到|知道了|在吗|你好|在不在|hi|hello|晚安|早安|谢谢)[。.!！~\s]*$", re.I)

# 来源不同，准入门槛不同：推测的东西要证明自己更值得留。
SOURCE_FLOOR = {"stated": 0.3, "inferred": 0.5, "uncertain": 0.6}

# 没有冒号的 key 会退化成一个不可复用的标签，这里给个按类型兜底的前缀
KIND_PREFIX = {"fact": "偏好", "episode": "共同", "todo": "待办", "self_state": "自我"}


def _clean(value: str) -> str:
    value = " ".join(str(value).strip().split())
    return value.strip("\"'“”‘’")


def _drop(reason: str, value: str, items: list[dict[str, Any]]) -> None:
    items.append({"kind": "-", "source": "dropped", "key": reason, "value": value, "importance": 0})


def parse_extraction(payload: Any, *, drops: list[dict[str, Any]] | None = None) -> list[dict[str, Any]]:
    items = payload.get("memories", []) if isinstance(payload, dict) else payload
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in items or []:
        if not isinstance(raw, dict):
            if drops is not None:
                _drop("不是对象", str(raw), drops)
            continue
        kind = KIND_MAP.get(str(raw.get("kind", "")).lower())
        if not kind:
            if drops is not None:
                _drop("类型不认识", str(raw.get("kind")), drops)
            continue
        value = _clean(raw.get("value", ""))
        if not value:
            if drops is not None:
                _drop("空", str(raw.get("key")), drops)
            continue
        if len(value) > 120:
            if drops is not None:
                _drop(f"过长 {len(value)}", value[:30], drops)
            continue
        if _FILLER.match(value):
            if drops is not None:
                _drop("寒暄/无信息", value, drops)
            continue

        source = str(raw.get("source", "stated")).lower()
        if source not in SOURCE_FLOOR:
            source = "inferred"
        subject = "self" if str(raw.get("subject", "user")).lower() == "self" else "user"
        key = _clean(raw.get("key", "")) or value
        if ":" not in key:
            # 提示词要求"主题:细节"。没给主题时按类型兜底，别退化成"其他:"
            key = f"{KIND_PREFIX[kind]}:{key}"
        key = key.lower()

        try:
            importance = float(raw.get("importance", 0.5))
        except (TypeError, ValueError):
            importance = 0.5
        importance = max(0.0, min(1.0, importance))

        floor = SOURCE_FLOOR[source]
        if importance < floor:
            if drops is not None:
                _drop(f"重要性 {importance:.2f} < {source} 门槛 {floor}", key, drops)
            continue
        if key in seen:
            if drops is not None:
                _drop("同批重复 key", key, drops)
            continue
        seen.add(key)

        out.append(
            {
                "kind": kind,
                "subject": subject,
                "key": key,
                "value": value,
                "source": source,
                "importance": importance,
                "confidence": {"stated": 0.95, "inferred": 0.6, "uncertain": 0.35}[source],
            }
        )

    per_kind: dict[str, int] = {}
    limited: list[dict[str, Any]] = []
    for item in out:
        count = per_kind.get(item["kind"], 0)
        if count >= 3:
            if drops is not None:
                _drop("该类已满 3 条", item["key"], drops)
            continue
        per_kind[item["kind"]] = count + 1
        limited.append(item)
    return limited


async def extract_memories(
    user_text: str,
    assistant_text: str,
    *,
    recent_context: str = "",
    trace: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Pull worth-remembering items out of one exchange.

    `trace` is filled with raw model output / parse error / timings. Failures are
    never silent: they land in trace['error'] so scripts/inspect_extract.py can
    tell 'the model said there was nothing' apart from 'the call broke'.
    """
    convo = f"用户：{user_text}\n{('最近对话：\n' + recent_context + '\n') if recent_context else ''}角色：{assistant_text}"
    started = time.perf_counter()
    raw = ""
    try:
        raw = await llm.complete(
            [{"role": "system", "content": EXTRACT_SYSTEM}, {"role": "user", "content": convo}],
            temperature=0.1,
            max_tokens=800,
        )
        payload = _loose_json(raw)
    except Exception as exc:
        if trace is not None:
            trace["error"] = f"{type(exc).__name__}: {exc}"
            trace["raw"] = raw
            trace["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        return []

    items = parse_extraction(payload, drops=trace.setdefault("drops", []) if trace is not None else None)
    if trace is not None:
        trace["raw"] = raw
        trace["payload"] = payload
        trace["parsed"] = items
        trace["elapsed_ms"] = int((time.perf_counter() - started) * 1000)
        trace["dropped"] = len(trace["drops"])
    return items