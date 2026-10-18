"""Anthropic Messages <-> OpenAI Chat translation.

Pure functions and one stateful stream translator. No HTTP, no framework: the
gateway shell owns the network so these shapes can be tested directly.

Supported: system prompts, text, tool definitions, tool_use/tool_result history,
streaming text deltas and streaming tool-call argument deltas.

Deliberately unsupported: images, prompt caching, thinking blocks, multi-account
routing, and provider-specific betas. Unsupported content is dropped or flattened;
the gateway never reports a tool success it did not receive from upstream.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from . import config


def _text(content: Any) -> str:
    """Flatten Anthropic text/tool_result blocks into one string."""
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if isinstance(block, str):
            parts.append(block)
        elif isinstance(block, dict):
            kind = block.get("type")
            if kind == "text":
                parts.append(str(block.get("text") or ""))
            elif kind == "tool_result":
                inner = block.get("content")
                text = _text(inner) if not isinstance(inner, str) else inner
                if block.get("is_error"):
                    text = "[tool_error] " + text
                if text:
                    parts.append(text)
    return "\n".join(part for part in parts if part)


def _tool_use_block(block: dict) -> dict:
    return {
        "id": str(block.get("id") or f"call_{uuid.uuid4().hex[:12]}"),
        "type": "function",
        "function": {
            "name": str(block.get("name") or ""),
            "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
        },
    }


def _messages(body: dict) -> list[dict]:
    """Anthropic messages -> OpenAI chat messages, preserving tool history."""
    out: list[dict] = []
    system = _text(body.get("system"))
    if system:
        out.append({"role": "system", "content": system})

    for item in body.get("messages") or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")

        # Claude Code's mid-conversation environment/context message.
        if role == "system":
            text = _text(content)
            if text:
                out.append({"role": "system", "content": text})
            continue
        if role not in ("user", "assistant"):
            continue

        if isinstance(content, str):
            out.append({"role": role, "content": content})
            continue

        blocks = [block for block in (content or []) if isinstance(block, dict)]
        if role == "assistant":
            texts: list[str] = []
            calls: list[dict] = []
            for block in blocks:
                if block.get("type") == "text":
                    texts.append(str(block.get("text") or ""))
                elif block.get("type") == "tool_use":
                    calls.append(_tool_use_block(block))
            message: dict[str, Any] = {"role": "assistant",
                                       "content": "\n".join(texts) if texts else None}
            if calls:
                message["tool_calls"] = calls
            out.append(message)
            continue

        # User turn: tool results must be adjacent to the assistant tool_calls.
        texts = []
        results = []
        for block in blocks:
            if block.get("type") == "text":
                texts.append(str(block.get("text") or ""))
            elif block.get("type") == "tool_result":
                result_text = _text(block.get("content"))
                if block.get("is_error"):
                    result_text = "[tool_error] " + result_text
                results.append({
                    "role": "tool",
                    "tool_call_id": str(block.get("tool_use_id") or ""),
                    "content": result_text,
                })
            elif block.get("type") == "image":
                texts.append("[image omitted]")
        out.extend(results)
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _tools(body: dict) -> list[dict]:
    out = []
    for tool in body.get("tools") or []:
        if not isinstance(tool, dict) or not tool.get("name"):
            continue
        schema = tool.get("input_schema") or {"type": "object", "properties": {}}
        out.append({
            "type": "function",
            "function": {
                "name": str(tool["name"]),
                "description": str(tool.get("description") or ""),
                "parameters": schema,
            },
        })
    return out


def _tool_choice(body: dict) -> Any:
    choice = body.get("tool_choice")
    if not isinstance(choice, dict):
        return None
    kind = choice.get("type")
    if kind == "auto":
        return "auto"
    if kind == "any":
        return "required"
    if kind == "none":
        return "none"
    if kind == "tool" and choice.get("name"):
        return {"type": "function", "function": {"name": str(choice["name"])}}
    return None


def to_openai(body: dict, *, model: str | None = None,
              max_output: int | None = None) -> dict:
    max_tokens = int(body.get("max_tokens") or 4096)
    cap = config.MAX_OUTPUT if max_output is None else max_output
    payload: dict[str, Any] = {
        "model": config.MODEL if model is None else model,
        "messages": _messages(body),
        "max_tokens": max(1, min(max_tokens, cap)),
        "temperature": body.get("temperature", 0.2),
        "stream": bool(body.get("stream")),
    }
    tools = _tools(body)
    if tools:
        payload["tools"] = tools
        choice = _tool_choice(body)
        if choice is not None:
            payload["tool_choice"] = choice
    if body.get("top_p") is not None:
        payload["top_p"] = body["top_p"]
    stops = body.get("stop_sequences")
    if isinstance(stops, list) and stops:
        payload["stop"] = stops[:4]
    if payload["stream"]:
        payload["stream_options"] = {"include_usage": True}
    return payload


# ---------------------------------------------------------------- response side


def _stop_reason(reason: str | None, has_tools: bool = False) -> str:
    if has_tools:
        return "tool_use"
    return {"length": "max_tokens", "stop": "end_turn", "tool_calls": "tool_use"}.get(
        str(reason), "end_turn")


def _tool_input(raw: Any) -> dict:
    if isinstance(raw, dict):
        return raw
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def openai_to_anthropic(data: dict, model: str, request_id: str) -> dict:
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    blocks: list[dict] = []
    text = message.get("content")
    if isinstance(text, str) and text:
        blocks.append({"type": "text", "text": text})
    calls = message.get("tool_calls") or []
    for call in calls:
        fn = call.get("function") or {}
        blocks.append({
            "type": "tool_use",
            "id": str(call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"),
            "name": str(fn.get("name") or ""),
            "input": _tool_input(fn.get("arguments")),
        })
    if not blocks:
        blocks.append({"type": "text", "text": ""})
    usage = data.get("usage") or {}
    return {
        "id": request_id,
        "type": "message",
        "role": "assistant",
        "model": model,
        "content": blocks,
        "stop_reason": _stop_reason(choice.get("finish_reason"), bool(calls)),
        "stop_sequence": None,
        "usage": {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
        },
    }


def sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode("utf-8")


class StreamTranslator:
    """Turn OpenAI chunk deltas into a valid Anthropic SSE event sequence."""

    def __init__(self, model: str, request_id: str):
        self.model = model
        self.request_id = request_id
        self.index = -1
        self.open_kind: str | None = None
        self.open_index: int | None = None
        self.tools: dict[int, dict] = {}
        self.finish_reason: str | None = None
        self.output_tokens = 0
        self.input_tokens = 0
        self.text_chars = 0

    def start(self, input_tokens: int = 0) -> list[bytes]:
        self.input_tokens = max(0, int(input_tokens or 0))
        return [sse("message_start", {
            "type": "message_start",
            "message": {
                "id": self.request_id, "type": "message", "role": "assistant",
                "model": self.model, "content": [], "stop_reason": None,
                "stop_sequence": None,
                "usage": {"input_tokens": self.input_tokens, "output_tokens": 0},
            },
        })]

    def _close(self) -> list[bytes]:
        if self.open_index is None:
            return []
        index = self.open_index
        self.open_index = None
        self.open_kind = None
        return [sse("content_block_stop", {"type": "content_block_stop", "index": index})]

    def handle(self, chunk: dict) -> list[bytes]:
        out: list[bytes] = []
        usage = chunk.get("usage") or {}
        if usage.get("completion_tokens"):
            self.output_tokens = int(usage["completion_tokens"])
        choices = chunk.get("choices") or []
        if not choices:
            return out
        choice = choices[0]
        delta = choice.get("delta") or {}

        piece = delta.get("content")
        if isinstance(piece, str) and piece:
            self.text_chars += len(piece)
            if self.open_kind != "text":
                out.extend(self._close())
                self.index += 1
                self.open_kind = "text"
                self.open_index = self.index
                out.append(sse("content_block_start", {
                    "type": "content_block_start", "index": self.index,
                    "content_block": {"type": "text", "text": ""},
                }))
            out.append(sse("content_block_delta", {
                "type": "content_block_delta", "index": self.open_index,
                "delta": {"type": "text_delta", "text": piece},
            }))

        for call in delta.get("tool_calls") or []:
            slot = int(call.get("index") or 0)
            state = self.tools.get(slot)
            fn = call.get("function") or {}
            if state is None:
                out.extend(self._close())
                self.index += 1
                state = {
                    "index": self.index,
                    "id": str(call.get("id") or f"toolu_{uuid.uuid4().hex[:12]}"),
                    "name": str(fn.get("name") or "tool"),
                }
                self.tools[slot] = state
                self.open_kind = "tool"
                self.open_index = self.index
                out.append(sse("content_block_start", {
                    "type": "content_block_start", "index": self.index,
                    "content_block": {
                        "type": "tool_use", "id": state["id"],
                        "name": state["name"], "input": {},
                    },
                }))
            elif self.open_index != state["index"]:
                # A tool result / text block interleaved before this tool's args.
                out.extend(self._close())
                self.open_kind = "tool"
                self.open_index = state["index"]
            args = fn.get("arguments")
            if args:
                out.append(sse("content_block_delta", {
                    "type": "content_block_delta", "index": state["index"],
                    "delta": {"type": "input_json_delta", "partial_json": str(args)},
                }))

        if choice.get("finish_reason"):
            self.finish_reason = str(choice["finish_reason"])
        return out

    def finish(self) -> list[bytes]:
        out = self._close()
        stop = "tool_use" if self.tools else _stop_reason(self.finish_reason)
        if not self.output_tokens:
            self.output_tokens = max(1, self.text_chars // 4)
        out.append(sse("message_delta", {
            "type": "message_delta",
            "delta": {"stop_reason": stop, "stop_sequence": None},
            "usage": {"output_tokens": self.output_tokens},
        }))
        out.append(sse("message_stop", {"type": "message_stop"}))
        return out
