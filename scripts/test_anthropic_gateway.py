"""Offline contract tests for the worker gateway.

    .\\.venv\\Scripts\\python.exe scripts\\test_anthropic_gateway.py

No network and no model: this pins the request/response shapes Claude Code
depends on, especially tool_use/tool_result history and the Anthropic SSE event
order, plus the inbound protocols, routing, fallback and redaction of the
gateway itself. The live check is `claude -p` through 127.0.0.1:15722.
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.gateway import anthropic_proxy, config  # noqa: E402
from server.gateway.anthropic_proxy import (MAX_OUTPUT, MODEL, StreamTranslator,  # noqa: E402
                                            openai_to_anthropic, sse, to_openai)

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


def event_name(raw: bytes) -> str:
    first = raw.decode("utf-8").splitlines()[0]
    return first.removeprefix("event: ")


def test_request_translation() -> None:
    print("\n[request -> OpenAI]")
    body = {
        "model": "claude-opus-5-5",
        "system": [{"type": "text", "text": "S1"}, {"type": "text", "text": "S2"}],
        "messages": [{"role": "user", "content": [{"type": "text", "text": "hi"}]}],
        "tools": [{"name": "Write", "description": "W",
                   "input_schema": {"type": "object",
                                    "properties": {"path": {"type": "string"}}}}],
        "tool_choice": {"type": "auto"},
        "max_tokens": 999999,
        "stream": True,
    }
    payload = to_openai(body)
    check("model is mapped to the local worker model", payload["model"] == MODEL, payload["model"])
    check("system blocks are joined", payload["messages"][0] == {"role": "system", "content": "S1\nS2"},
          str(payload["messages"][0]))
    check("user text is preserved", payload["messages"][1] == {"role": "user", "content": "hi"})
    check("tools are translated", payload["tools"][0]["function"]["name"] == "Write")
    check("tool schema is preserved",
          payload["tools"][0]["function"]["parameters"]["properties"]["path"]["type"] == "string")
    check("tool_choice auto maps to auto", payload["tool_choice"] == "auto")
    check("max_tokens is capped", payload["max_tokens"] == MAX_OUTPUT, str(payload["max_tokens"]))
    check("stream requests ask for usage", payload["stream_options"] == {"include_usage": True})

    body["tool_choice"] = {"type": "any"}
    check("tool_choice any maps to required", to_openai(body)["tool_choice"] == "required")
    body["tool_choice"] = {"type": "tool", "name": "Write"}
    check("tool_choice tool maps to a function",
          to_openai(body)["tool_choice"] == {"type": "function", "function": {"name": "Write"}})
    body["tool_choice"] = {"type": "none"}
    check("tool_choice none maps to none", to_openai(body)["tool_choice"] == "none")


def test_tool_history() -> None:
    print("\n[tool history]")
    body = {"messages": [
        {"role": "assistant", "content": [
            {"type": "text", "text": "calling"},
            {"type": "tool_use", "id": "t1", "name": "Write", "input": {"path": "x"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "t1", "content": "ok"},
            {"type": "text", "text": "continue"},
        ]},
        {"role": "system", "content": [{"type": "text", "text": "env"}]},
    ]}
    messages = to_openai(body)["messages"]
    check("assistant text is preserved", messages[0]["content"] == "calling")
    check("assistant tool_use becomes tool_calls",
          messages[0]["tool_calls"][0]["id"] == "t1"
          and json.loads(messages[0]["tool_calls"][0]["function"]["arguments"]) == {"path": "x"})
    check("tool_result becomes a tool message",
          messages[1] == {"role": "tool", "tool_call_id": "t1", "content": "ok"}, str(messages[1]))
    check("text after a tool_result stays a user turn",
          messages[2] == {"role": "user", "content": "continue"}, str(messages[2]))
    check("mid-conversation system is preserved",
          messages[3] == {"role": "system", "content": "env"}, str(messages[3]))


def test_response_translation() -> None:
    print("\n[OpenAI -> Anthropic]")
    data = {"choices": [{"message": {"content": "OK"}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 3}}
    out = openai_to_anthropic(data, "claude-opus-5-5", "msg_test")
    check("text response becomes one text block",
          out["content"] == [{"type": "text", "text": "OK"}], str(out["content"]))
    check("stop reason is end_turn", out["stop_reason"] == "end_turn")
    check("usage is mapped", out["usage"] == {"input_tokens": 10, "output_tokens": 3})

    data = {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": "c1", "function": {"name": "Write", "arguments": '{"path":"x"}'}}]},
        "finish_reason": "tool_calls"}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5}}
    out = openai_to_anthropic(data, "claude-opus-5-5", "msg_test")
    check("tool call becomes tool_use",
          out["content"][0] == {"type": "tool_use", "id": "c1", "name": "Write", "input": {"path": "x"}},
          str(out["content"]))
    check("tool call stop reason", out["stop_reason"] == "tool_use")

    data = {"choices": [{"message": {"content": "cut"}, "finish_reason": "length"}]}
    check("length maps to max_tokens",
          openai_to_anthropic(data, "m", "id")["stop_reason"] == "max_tokens")


def test_stream_translation() -> None:
    print("\n[Anthropic SSE stream]")
    translator = StreamTranslator("claude-opus-5-5", "msg_stream")
    start = translator.start(123)
    check("message_start carries real input tokens",
          json.loads(start[0].decode().splitlines()[1][5:])["message"]["usage"]["input_tokens"] == 123)

    text_events = translator.handle({"choices": [{"delta": {"content": "He"}}]})
    names = [event_name(event) for event in text_events]
    check("text opens a text block then streams a delta",
          names == ["content_block_start", "content_block_delta"], str(names))

    tool_events = translator.handle({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "id": "c1", "function": {"name": "Write", "arguments": '{"a"'}}]}}]})
    names = [event_name(event) for event in tool_events]
    check("tool call closes text and opens tool_use",
          names == ["content_block_stop", "content_block_start", "content_block_delta"], str(names))

    more = translator.handle({"choices": [{"delta": {"tool_calls": [
        {"index": 0, "function": {"arguments": ':1}'}}]}}]})
    check("tool arguments keep streaming",
          [event_name(event) for event in more] == ["content_block_delta"], str(more))

    finish = translator.finish()
    names = [event_name(event) for event in finish]
    check("finish closes the tool block and stops the message",
          names == ["content_block_stop", "message_delta", "message_stop"], str(names))
    delta = json.loads(finish[1].decode().splitlines()[1][5:])
    check("finish reports tool_use", delta["delta"]["stop_reason"] == "tool_use", str(delta))
    check("sse frames have both event and data lines",
          sse("ping", {"type": "ping"}).decode().startswith("event: ping\ndata: "))

    # A text-only stream must still close its block and end cleanly.
    translator = StreamTranslator("m", "id")
    translator.start(1)
    translator.handle({"choices": [{"delta": {"content": "ok"}, "finish_reason": "stop"}]})
    names = [event_name(event) for event in translator.finish()]
    check("text-only finish is valid", names == ["content_block_stop", "message_delta", "message_stop"],
          str(names))


KEY = "sk-gateway-test-000000"
_ORIGINAL_SETTINGS = config.SETTINGS
_ORIGINAL_NEW_CLIENT = anthropic_proxy._new_client


def gateway(handler, *, fallback: bool = False) -> TestClient:
    """Point the app at a mock upstream. Callers must call restore() afterwards."""
    config.SETTINGS = config.Settings(
        upstream=config.Upstream("primary", "http://primary.test/v1", KEY),
        fallback=(config.Upstream("fallback", "http://fallback.test/v1", "sk-fallback-000000")
                  if fallback else None),
        responses_upstream="http://primary.test/v1/responses",
        model="step-5-preview",
        cheap_model="step-3.7-flash",
        routes={"claude-opus-5-5": "step-5-preview"},
        max_output=MAX_OUTPUT,
        timeout=5.0,
    )
    transport = httpx.MockTransport(handler)
    anthropic_proxy._new_client = lambda: httpx.AsyncClient(transport=transport)
    return TestClient(anthropic_proxy.app)


def restore() -> None:
    config.SETTINGS = _ORIGINAL_SETTINGS
    anthropic_proxy._new_client = _ORIGINAL_NEW_CLIENT


def chat_completion(text: str = "OK") -> httpx.Response:
    return httpx.Response(200, json={
        "id": "chatcmpl-1",
        "choices": [{"message": {"content": text}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 12, "completion_tokens": 3},
    })


def test_chat_inbound() -> None:
    print("\n[chat inbound]")
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return chat_completion()

    client = gateway(handler)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "claude-haiku-4-5",
            "messages": [{"role": "user", "content": "hi"}],
        })
    finally:
        restore()
    check("chat non-streaming relays the upstream body",
          response.status_code == 200 and response.json()["id"] == "chatcmpl-1", response.text)
    check("chat reports the serving upstream via header",
          response.headers.get("x-aqi-upstream") == "primary",
          str(response.headers.get("x-aqi-upstream")))
    forwarded = json.loads(calls[0].content)
    check("chat inbound is model-routed to the cheap tier",
          forwarded["model"] == "step-3.7-flash", str(forwarded.get("model")))
    check("chat inbound keeps the caller's other fields",
          forwarded["messages"] == [{"role": "user", "content": "hi"}], str(forwarded))
    check("chat upstream sees the gateway key, not the client's",
          calls[0].headers["authorization"] == f"Bearer {KEY}",
          calls[0].headers["authorization"])
    check("chat upstream path is chat/completions",
          str(calls[0].url) == "http://primary.test/v1/chat/completions", str(calls[0].url))

    stream_bytes = (b'data: {"choices":[{"delta":{"content":"He"}}]}\n\n'
                    b'data: {"choices":[{"delta":{},"finish_reason":"stop"}]}\n\n'
                    b'data: [DONE]\n\n')

    def stream_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-type": "text/event-stream"},
                              content=stream_bytes)

    client = gateway(stream_handler)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "step-5-preview", "stream": True, "messages": []})
    finally:
        restore()
    check("chat streaming preserves the SSE bytes", response.content == stream_bytes,
          repr(response.content))
    check("chat streaming keeps the event-stream content type",
          response.headers.get("content-type", "").startswith("text/event-stream"),
          response.headers.get("content-type", ""))
    check("chat streaming reports the serving upstream",
          response.headers.get("x-aqi-upstream") == "primary")


def test_responses_inbound() -> None:
    print("\n[responses inbound]")
    calls: list[httpx.Request] = []
    stream_bytes = b'event: response.output_text.delta\ndata: {"delta":"Hi"}\n\n'

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        body = json.loads(request.content)
        if body.get("stream"):
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                  content=stream_bytes)
        return httpx.Response(200, headers={"content-type": "application/json"},
                              content=b'{"id":"resp_1","object":"response"}')

    client = gateway(handler)
    try:
        raw = b'{"model":"step-5-preview","input":"hi","stream":false}'
        response = client.post("/v1/responses", content=raw,
                               headers={"content-type": "application/json"})
    finally:
        restore()
    check("responses relays the body untouched", calls[0].content == raw, repr(calls[0].content))
    check("responses preserves status and body",
          response.status_code == 200 and response.json()["id"] == "resp_1", response.text)
    check("responses targets the responses upstream",
          str(calls[0].url) == "http://primary.test/v1/responses", str(calls[0].url))
    check("responses preserves the JSON content type",
          response.headers.get("content-type", "").startswith("application/json"),
          response.headers.get("content-type", ""))

    client = gateway(handler)
    try:
        raw = b'{"model":"step-5-preview","input":"hi","stream":true}'
        response = client.post("/v1/responses", content=raw,
                               headers={"content-type": "application/json"})
    finally:
        restore()
    check("responses streaming preserves the SSE bytes", response.content == stream_bytes,
          repr(response.content))
    check("responses streaming keeps the event-stream content type",
          response.headers.get("content-type", "").startswith("text/event-stream"))

    def throttled(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"content-type": "application/json"},
                              content=b'{"error":{"message":"slow down"}}')

    client = gateway(throttled)
    try:
        response = client.post("/v1/responses", content=b'{"model":"m","input":"hi"}',
                               headers={"content-type": "application/json"})
    finally:
        restore()
    check("responses preserves an upstream 4xx status and body",
          response.status_code == 429 and response.json()["error"]["message"] == "slow down",
          response.text)


def test_model_routing() -> None:
    print("\n[model routing]")
    table = {"claude-opus-5-5": "step-5-preview"}
    check("flash routes to the cheap model",
          config.route_model("deepseek-flash", routes=table, main="main", cheap="cheap") == "cheap")
    check("haiku routes to the cheap model",
          config.route_model("claude-haiku-4-5", routes=table, main="main", cheap="cheap") == "cheap")
    check("lite routes to the cheap model",
          config.route_model("tiny-lite-v1", routes=table, main="main", cheap="cheap") == "cheap")
    check("unknown ids route to the main model",
          config.route_model("claude-opus-5-5", routes={}, main="main", cheap="cheap") == "main")
    check("the explicit table wins over keywords",
          config.route_model("custom-flash", routes={"custom-flash": "pinned"},
                             main="main", cheap="cheap") == "pinned")
    check("a missing model routes to the main model",
          config.route_model(None, routes={}, main="main", cheap="cheap") == "main")

    seen: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return chat_completion()

    client = gateway(handler)
    try:
        response = client.post("/v1/messages", json={
            "model": "claude-haiku-4-5", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}]})
    finally:
        restore()
    check("anthropic inbound routes flash-class ids upstream",
          seen[0]["model"] == "step-3.7-flash", str(seen[0].get("model")))
    check("anthropic response echoes the requested model",
          response.json()["model"] == "claude-haiku-4-5", response.text)


def test_fallback() -> None:
    print("\n[upstream fallback]")
    calls: list[httpx.Request] = []

    def five_hundred(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "primary.test":
            return httpx.Response(500, json={"error": "primary down"})
        return chat_completion()

    client = gateway(five_hundred, fallback=True)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "step-5-preview", "messages": []})
    finally:
        restore()
    check("5xx falls back to the secondary upstream", response.status_code == 200, response.text)
    check("fallback is reported in x-aqi-upstream",
          response.headers.get("x-aqi-upstream") == "fallback",
          str(response.headers.get("x-aqi-upstream")))

    def connection_error(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "primary.test":
            raise httpx.ConnectError("no route", request=request)
        return chat_completion()

    client = gateway(connection_error, fallback=True)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "step-5-preview", "messages": []})
    finally:
        restore()
    check("connection errors fall back", response.status_code == 200, response.text)

    calls.clear()

    def four_hundred(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.host == "primary.test":
            return httpx.Response(400, json={"error": {"message": "bad request"}})
        return chat_completion()

    client = gateway(four_hundred, fallback=True)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "step-5-preview", "messages": []})
    finally:
        restore()
    check("4xx never falls back",
          response.status_code == 400 and all(c.url.host == "primary.test" for c in calls),
          f"{response.status_code} {[c.url.host for c in calls]}")

    def anthropic_fallback(request: httpx.Request) -> httpx.Response:
        if request.url.host == "primary.test":
            return httpx.Response(503, json={"error": "busy"})
        return chat_completion("from fallback")

    client = gateway(anthropic_fallback, fallback=True)
    try:
        response = client.post("/v1/messages", json={
            "model": "claude-opus-5-5", "max_tokens": 16,
            "messages": [{"role": "user", "content": "hi"}]})
    finally:
        restore()
    check("anthropic 5xx falls back and translates the fallback response",
          response.status_code == 200
          and response.json()["content"][0]["text"] == "from fallback", response.text)

    def all_down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route", request=request)

    client = gateway(all_down, fallback=True)
    try:
        response = client.post("/v1/chat/completions", json={
            "model": "step-5-preview", "messages": []})
    finally:
        restore()
    check("all upstreams failing is a real 502, not a fake success",
          response.status_code == 502 and "upstream_error" in response.text, response.text)


def test_models_endpoint() -> None:
    print("\n[/v1/models]")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"id": "upstream-only-model"}]})

    client = gateway(handler)
    try:
        response = client.get("/v1/models")
    finally:
        restore()
    payload = response.json()
    ids = [item["id"] for item in payload["data"]]
    check("/v1/models is an OpenAI-style list",
          payload["object"] == "list" and payload["has_more"] is False, str(payload)[:200])
    check("/v1/models lists the main and cheap models",
          {"step-5-preview", "step-3.7-flash"} <= set(ids), str(ids))
    check("/v1/models merges the upstream catalog", "upstream-only-model" in ids, str(ids))
    check("/v1/models maps every id to an upstream model",
          all(item["upstream_model"] for item in payload["data"]), str(payload["data"])[:200])
    check("/v1/models has no duplicate ids", len(ids) == len(set(ids)), str(ids))

    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("down", request=request)

    client = gateway(down)
    try:
        response = client.get("/v1/models")
    finally:
        restore()
    check("/v1/models survives an unreachable upstream",
          response.status_code == 200 and bool(response.json()["data"]), response.text)


def test_redaction() -> None:
    print("\n[redacted logging]")
    records: list[str] = []

    class Capture(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record.getMessage())

    capture = Capture()
    anthropic_proxy.logger.addHandler(capture)
    anthropic_proxy.logger.setLevel(logging.INFO)
    try:
        def leaking(request: httpx.Request) -> httpx.Response:
            if request.url.host == "primary.test":
                return httpx.Response(500, headers={"content-type": "text/plain"},
                                      content=("rejected " + KEY
                                               + " Authorization: Bearer " + KEY).encode())
            return chat_completion()

        client = gateway(leaking, fallback=True)
        try:
            response = client.post("/v1/chat/completions", json={
                "model": "step-5-preview", "messages": []})
        finally:
            restore()
    finally:
        anthropic_proxy.logger.removeHandler(capture)
        anthropic_proxy.logger.setLevel(logging.WARNING)
    text = "\n".join(records)
    check("a recovered upstream failure is logged with its status",
          "upstream primary returned 500" in text, text)
    check("logs never contain the gateway key", KEY not in text, text)
    check("logs redact the credential-shaped snippet", "***" in text, text)
    check("logs record the routed model, not the prompt", "model=step-5-preview" in text, text)
    check("logs record upstream token usage from a buffered body",
          "prompt_tokens=12" in text and "completion_tokens=3" in text, text)


def test_health() -> None:
    print("\n[/health]")
    client = gateway(lambda request: httpx.Response(200, json={}))
    try:
        response = client.get("/health")
    finally:
        restore()
    payload = response.json()
    check("/health reports protocols, routing and upstreams",
          payload["protocols"] == {"anthropic_messages": True, "chat_completions": True,
                                   "responses": True}
          and payload["upstream"] == "http://primary.test/v1"
          and payload["fallback"] is None
          and payload["routes"] == {"claude-opus-5-5": "step-5-preview"},
          str(payload))


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    # Request logs are exercised by the redaction case; silence them elsewhere.
    anthropic_proxy.logger.setLevel(logging.WARNING)
    test_request_translation()
    test_tool_history()
    test_response_translation()
    test_stream_translation()
    test_chat_inbound()
    test_responses_inbound()
    test_model_routing()
    test_fallback()
    test_models_endpoint()
    test_redaction()
    test_health()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        for name in failed:
            print(f"  - {name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
