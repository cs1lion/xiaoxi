"""Reserved, declarative motion metadata; never executable avatar instructions.

The streaming filter precedes sentence/bracket parsing so JSON punctuation is
never spoken or mistaken for a sentence boundary. All offsets below are Python
Unicode codepoints in the final, sanitized text sent with the audio event.
"""
from __future__ import annotations

import json
import math
from typing import Any


INTENT_GESTURES = {
    "greeting": "wave", "farewell": "wave",
    "explanation": "palm", "question": "palm", "reassurance": "palm",
    "disagreement": "deny", "self_reference": "self", "neutral": None,
}
MOTION_INSTRUCTION = (
    "每句口头文本之前必须输出 1 到 2 条保留的 motion 元数据，通常只需 1 条；"
    "不要省略，包括只有一句话的简短回复。情绪标签仍必须放在整条回复最前面。"
    "格式为 [motion:{\"intent\":\"self_reference\","
    "\"anchor\":\"我觉得\",\"strength\":0.4}]，JSON 只能使用 intent、anchor、strength。"
    "intent 只能是 greeting、farewell、explanation、question、reassurance、"
    "disagreement、self_reference、neutral；根据真实语义选择，别为每句话硬加手势。"
    "anchor 必须逐字取自紧随标签的该句口头文本，不包含标签。"
    "strength 为 0.15 到 0.75 的数字，默认 0.4；每句最多两个轻微动作。"
    "没有自然、合适的手势时，必须输出 [motion:{\"intent\":\"neutral\"}]，"
    "再输出该句口头文本；neutral 表示不做手势，不需要 anchor 或 strength。"
    "这些标签不会说出口，除情绪和 motion 外不要写其他动作标记。\n"
    "示例：[认真][motion:{\"intent\":\"self_reference\",\"anchor\":\"我觉得\","
    "\"strength\":0.35}]我觉得，这个办法可以试试。"
)


class MotionMetadataFilter:
    """Incrementally remove only the reserved ASCII ``[motion:`` namespace.

    Return text/metadata tokens in their source order, even with single-character
    deltas. JSON strings may contain brackets and escapes. Malformed complete
    blocks are discarded; an unfinished recognized prefix or block is withheld
    at EOF. Ordinary completed brackets (including ``[motion]``) are untouched.
    An unclosed metadata string/block conservatively consumes its remaining tail.
    """

    PREFIX = "[motion:"
    MAX_METADATA_CHARS = 4096

    def __init__(self) -> None:
        self._candidate = ""
        self._reserved = False
        self._body = ""
        self._overflow = False
        self._quoted = False
        self._escaped = False
        self._arrays = 0

    def feed(self, char: str) -> list[tuple[str, Any]]:
        if self._reserved:
            if not self._quoted and char == "]" and self._arrays == 0:
                try:
                    value = None if self._overflow else json.loads(self._body)
                except (ValueError, TypeError, RecursionError):
                    value = None
                self._reserved = False
                self._body = ""
                self._overflow = False
                return [("motion", value)]
            if not self._overflow:
                self._body += char
                if len(self._body) > self.MAX_METADATA_CHARS:
                    self._body = ""
                    self._overflow = True
            if self._quoted:
                if self._escaped:
                    self._escaped = False
                elif char == "\\":
                    self._escaped = True
                elif char == '"':
                    self._quoted = False
            elif char == '"':
                self._quoted = True
            elif char == "[":
                self._arrays += 1
            elif char == "]" and self._arrays:
                self._arrays -= 1
            return []
        if self._candidate:
            candidate = self._candidate + char
            if self.PREFIX.startswith(candidate):
                self._candidate = candidate
                if candidate == self.PREFIX:
                    self._candidate = ""
                    self._reserved = True
                    self._quoted = self._escaped = False
                    self._arrays = 0
                return []
            old = self._candidate
            self._candidate = ""
            return [("text", old)] + self.feed(char)
        if char == "[":
            self._candidate = char
            return []
        return [("text", char)]

    def finish(self) -> list[tuple[str, Any]]:
        # Prefix candidates are exact partial spellings of the reserved token.
        # Let the existing bracket parser handle a bare opening square bracket.
        tail = "[" if self._candidate == "[" else ""
        self._candidate = ""
        self._reserved = False
        self._body = ""
        self._overflow = self._quoted = self._escaped = False
        self._arrays = 0
        return [("text", tail)] if tail else []


def strip_motion_metadata(text: str) -> str:
    parser = MotionMetadataFilter()
    result: list[str] = []
    for char in text:
        result.extend(value for kind, value in parser.feed(char) if kind == "text")
    result.extend(value for kind, value in parser.finish() if kind == "text")
    return "".join(result)


def performance_plan(text: str, annotations: list[Any]) -> dict[str, Any]:
    cues: list[dict[str, Any]] = []
    accepted = 0
    for item in annotations:
        if not isinstance(item, dict) or set(item) - {"intent", "anchor", "strength"}:
            continue
        intent = item.get("intent")
        if not isinstance(intent, str) or intent not in INTENT_GESTURES:
            continue
        if intent == "neutral":
            return {"version": 1, "source": "llm", "cues": []}
        anchor = item.get("anchor")
        if not isinstance(anchor, str) or not anchor or len(anchor) > len(text):
            continue
        start = text.find(anchor)
        if start < 0:
            continue
        strength = item.get("strength", 0.4)
        if isinstance(strength, bool) or not isinstance(strength, (int, float)) or not math.isfinite(strength):
            strength = 0.4
        cue = {"intent": intent, "gesture": INTENT_GESTURES[intent], "anchor": anchor,
               "start_char": start, "end_char": start + len(anchor),
               "strength": min(0.75, max(0.15, float(strength)))}
        if cue not in cues and len(cues) < 2:
            cues.append(cue)
        accepted += 1
        if accepted >= 2:
            break
    return {"version": 1, "source": "llm" if cues else "fallback", "cues": cues}


def slice_performance(plan: dict[str, Any], start: int, end: int) -> dict[str, Any]:
    """Rebase whole anchors into one lossless audio slice; never invent an anchor.

    A cross-slice anchor is omitted while preserving llm provenance, so keyword
    fallback cannot add an unrelated action to an explicitly planned sentence.
    """
    return {"version": 1, "source": plan["source"], "cues": [
        dict(cue, start_char=cue["start_char"] - start, end_char=cue["end_char"] - start)
        for cue in plan["cues"] if start <= cue["start_char"] < cue["end_char"] <= end
    ]}
