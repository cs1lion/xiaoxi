"""Unified loopback gateway for the project's CLI workers.

Three inbound protocols on 127.0.0.1:15722:

- `POST /v1/messages`          Anthropic Messages, translated to OpenAI Chat.
- `POST /v1/chat/completions`  OpenAI Chat, model-routed then relayed.
- `POST /v1/responses`         OpenAI Responses, relayed untouched.

Design and status: docs/GATEWAY_DESIGN.md. This is a gateway, not a scheduler:
it never decides who works or when, and it never turns a failed upstream call
into a success.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse

from . import config
from .config import Upstream
from .config import MAX_OUTPUT, MODEL, route_model  # noqa: F401  (public surface)
from .translate import StreamTranslator, openai_to_anthropic, sse, to_openai

app = FastAPI(title="xiaoxi-worker-gateway")

logger = logging.getLogger("aqi.gateway")
if not logger.handlers:
    # uvicorn configures only its own loggers, so without a handler the request
    # log never reaches the terminal in production.
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(levelname)s %(name)s %(message)s"))
    logger.addHandler(_handler)
    logger.setLevel(logging.INFO)


# ---------------------------------------------------------------- observability

_SECRET_FIELD = re.compile(
    r"(?i)\b(authorization|api[_-]?key|x-api-key|token|password|secret|cookie)\b"
    r"\s*[:=]\s*\S+")
_BEARER = re.compile(r"(?i)\bbearer\s+\S+")


def _configured_keys() -> tuple[str, ...]:
    settings = config.SETTINGS
    keys = [settings.upstream.key]
    if settings.fallback is not None:
        keys.append(settings.fallback.key)
    # Short values are placeholders like `local`, and replacing them verbatim
    # would mangle unrelated words in an error message.
    return tuple({key for key in keys if len(key) >= 6})


def redact(text: str, limit: int = 200) -> str:
    """Strip credential-shaped text before it reaches a log line or an error body."""
    clean = _SECRET_FIELD.sub(r"\1: ***", text or "")
    clean = _BEARER.sub("Bearer ***", clean)
    for key in _configured_keys():
        clean = clean.replace(key, "***")
    return " ".join(clean.split())[:limit]


def log_call(method: str, path: str, client: str, requested_model: str,
             upstream_model: str, upstream_name: str, status: int, duration_ms: float,
             prompt_tokens: int | None = None, completion_tokens: int | None = None,
             error: str = "") -> None:
    logger.info(
        "%s %s client=%s model=%s upstream_model=%s upstream=%s status=%s "
        "duration_ms=%.1f prompt_tokens=%s completion_tokens=%s%s",
        method, path, client, requested_model or "-", upstream_model or "-",
        upstream_name or "-", status, duration_ms,
        "-" if prompt_tokens is None else prompt_tokens,
        "-" if completion_tokens is None else completion_tokens,
        f" error={redact(error)}" if error else "",
    )


def _client_host(request: Request) -> str:
    if request.client is None:
        return "-"
    return f"{request.client.host}:{request.client.port}"


def _media_type(response: httpx.Response, default: str) -> str:
    raw = response.headers.get("content-type") or default
    return raw.split(";")[0].strip() or default


# ---------------------------------------------------------------- upstream I/O

_DROP_REQUEST_HEADERS = {
    "host", "content-length", "transfer-encoding", "connection", "accept-encoding",
    "authorization", "x-api-key", "api-key", "cookie",
}


def _relay_headers(request: Request) -> dict[str, str]:
    return {name: value for name, value in request.headers.items()
            if name.lower() not in _DROP_REQUEST_HEADERS}


def _new_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=httpx.Timeout(config.SETTINGS.timeout, connect=10.0))


def _chat_targets() -> list[tuple[Upstream, str]]:
    return [(upstream, f"{upstream.base_url}/chat/completions")
            for upstream in config.SETTINGS.upstreams]


def _responses_targets() -> list[tuple[Upstream, str]]:
    settings = config.SETTINGS
    targets = [(settings.upstream, settings.responses_upstream)]
    if settings.fallback is not None:
        targets.append((settings.fallback, f"{settings.fallback.base_url}/responses"))
    return targets


@dataclass
class Forwarded:
    """Outcome of one outbound request. With stream=True, `response` is still open."""
    client: httpx.AsyncClient | None = None
    response: httpx.Response | None = None
    upstream: Upstream | None = None
    status: int = 502
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.response is not None

    @property
    def delivered(self) -> bool:
        return self.response is not None and self.status < 400


async def _close(fwd: Forwarded) -> None:
    if fwd.response is not None:
        await fwd.response.aclose()
    if fwd.client is not None:
        await fwd.client.aclose()


async def _failure(fwd: Forwarded) -> tuple[int, str, str]:
    """(status, detail, upstream name) for a request that did not succeed."""
    name = fwd.upstream.name if fwd.upstream else ""
    if fwd.response is None:
        return fwd.status, fwd.error, name
    detail = (await fwd.response.aread()).decode("utf-8", "replace")
    await _close(fwd)
    return fwd.status, detail, name


async def _forward(targets: list[tuple[Upstream, str]], body: bytes,
                   headers: dict[str, str], *, stream: bool) -> Forwarded:
    """First upstream that answers. Connection errors and 5xx fall through; 4xx stops.

    A failed generation is never retried into a success: the fallback only runs
    when nothing has been sent to the client yet.
    """
    client = _new_client()
    last = Forwarded(status=502, error="no upstream configured")
    for index, (upstream, url) in enumerate(targets):
        try:
            request = client.build_request(
                "POST", url, content=body,
                headers={**headers, **upstream.headers()})
            response = await client.send(request, stream=stream)
        except httpx.HTTPError as exc:
            last = Forwarded(upstream=upstream, status=502,
                             error=f"upstream {upstream.name} unavailable: {type(exc).__name__}")
            logger.warning("upstream %s unavailable: %s", upstream.name, type(exc).__name__)
            continue
        if response.status_code >= 500 and index + 1 < len(targets):
            detail = (await response.aread()).decode("utf-8", "replace")
            await response.aclose()
            last = Forwarded(upstream=upstream, status=response.status_code, error=detail)
            logger.warning("upstream %s returned %s: %s",
                           upstream.name, response.status_code, redact(detail))
            continue
        return Forwarded(client=client, response=response, upstream=upstream,
                         status=response.status_code)
    await client.aclose()
    return last


async def _buffered(fwd: Forwarded) -> Response:
    try:
        content = await fwd.response.aread()
    finally:
        await _close(fwd)
    return Response(content, status_code=fwd.status, media_type=_media_type(fwd.response, "application/json"),
                    headers={"x-aqi-upstream": fwd.upstream.name})


def _relayed(fwd: Forwarded, done: Callable[[str], None]) -> StreamingResponse:
    async def body() -> AsyncIterator[bytes]:
        failure = ""
        try:
            async for chunk in fwd.response.aiter_bytes():
                yield chunk
        except httpx.HTTPError as exc:
            # The client already holds a status and partial bytes. Truncating is
            # the only honest signal; an error frame would corrupt the SSE bytes.
            failure = f"relay failed: {type(exc).__name__}"
            raise
        finally:
            await _close(fwd)
            done(failure)

    return StreamingResponse(
        body(), status_code=fwd.status,
        media_type=_media_type(fwd.response, "application/octet-stream"),
        headers={"x-aqi-upstream": fwd.upstream.name, "cache-control": "no-cache"})


def _error(status: int, message: str, upstream: str = "") -> JSONResponse:
    headers = {"x-aqi-upstream": upstream} if upstream else None
    return JSONResponse(status_code=status, headers=headers, content={
        "type": "error",
        "error": {
            "type": "api_error" if status >= 500 else "invalid_request_error",
            "message": redact(message, 400),
        },
    })


def _peek(raw: bytes) -> dict | None:
    try:
        body = json.loads(raw)
    except ValueError:
        return None
    return body if isinstance(body, dict) else None


def _usage(body: bytes) -> tuple[int | None, int | None]:
    """Token counts from a buffered OpenAI-shaped body, when it has any."""
    payload = _peek(body)
    usage = (payload or {}).get("usage") or {}
    if not isinstance(usage, dict):
        return None, None
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


# ---------------------------------------------------------------- HTTP surface

SERVED_ALIASES = ("claude-opus-5-5", "deepseek-flash")  # ids the shipped shims send


@app.get("/health")
async def health():
    settings = config.SETTINGS
    return {
        "ok": True,
        "upstream": settings.upstream.base_url,
        "fallback": settings.fallback.base_url if settings.fallback else None,
        "responses_upstream": settings.responses_upstream,
        "model": settings.model,
        "cheap_model": settings.cheap_model,
        "routes": dict(settings.routes),
        "cheap_hints": list(config.CHEAP_HINTS),
        "protocols": {
            "anthropic_messages": True,
            "chat_completions": True,
            "responses": True,
        },
        "tools": True,
        "max_output_tokens": settings.max_output,
    }


@app.get("/v1/models")
async def models():
    settings = config.SETTINGS
    served = [settings.model, settings.cheap_model,
              *settings.routes, *settings.routes.values(), *SERVED_ALIASES]

    upstream_ids: list[str] = []
    try:
        async with _new_client() as client:
            response = await client.get(f"{settings.upstream.base_url}/models",
                                        headers=settings.upstream.headers(), timeout=1.5)
            if response.status_code == 200:
                payload = response.json()
                data = payload.get("data") if isinstance(payload, dict) else None
                if isinstance(data, list):
                    upstream_ids = [str(item["id"]) for item in data
                                    if isinstance(item, dict) and item.get("id")]
    except (httpx.HTTPError, ValueError):
        pass  # A down upstream must not break discovery.

    return {
        "object": "list",
        "has_more": False,
        "data": [{"id": model_id, "object": "model", "type": "model",
                  "display_name": model_id, "upstream_model": settings.route(model_id)}
                 for model_id in dict.fromkeys([*served, *upstream_ids])],
    }


@app.post("/v1/messages/count_tokens")
async def count_tokens(request: Request):
    body = await request.json()
    chars = len(json.dumps(body, ensure_ascii=False))
    return {"input_tokens": max(1, chars // 4)}


@app.post("/v1/messages")
async def messages(request: Request):
    started = time.perf_counter()
    try:
        body = await request.json()
    except ValueError:
        return _error(400, "request body is not JSON")
    if not isinstance(body, dict):
        return _error(400, "request body must be a JSON object")
    requested = str(body.get("model") or "claude")
    upstream_model = config.route_model(requested)
    payload = to_openai(body, model=upstream_model, max_output=config.SETTINGS.max_output)
    if not payload["messages"]:
        return _error(400, "no user message")
    request_id = "msg_" + uuid.uuid4().hex[:24]
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    client = _client_host(request)

    if not payload["stream"]:
        fwd = await _forward(_chat_targets(), raw, {}, stream=False)
        if not fwd.delivered:
            status, detail, name = await _failure(fwd)
            log_call("POST", "/v1/messages", client, requested, upstream_model,
                     name or "-", status, (time.perf_counter() - started) * 1000,
                     error=detail)
            return _error(status, detail, name)
        content = await fwd.response.aread()
        await _close(fwd)
        try:
            data = json.loads(content)
        except ValueError:
            data = None
        if not isinstance(data, dict):
            log_call("POST", "/v1/messages", client, requested, upstream_model,
                     fwd.upstream.name, 502, (time.perf_counter() - started) * 1000,
                     error="upstream returned a non-JSON body")
            return _error(502, "upstream returned a non-JSON body", fwd.upstream.name)
        usage = data.get("usage") or {}
        log_call("POST", "/v1/messages", client, requested, upstream_model,
                 fwd.upstream.name, fwd.status, (time.perf_counter() - started) * 1000,
                 prompt_tokens=usage.get("prompt_tokens"),
                 completion_tokens=usage.get("completion_tokens"))
        return JSONResponse(openai_to_anthropic(data, requested, request_id),
                            headers={"x-aqi-upstream": fwd.upstream.name})

    fwd = await _forward(_chat_targets(), raw, {}, stream=True)
    headers = {"cache-control": "no-cache", "x-accel-buffering": "no"}
    if fwd.upstream is not None:
        headers["x-aqi-upstream"] = fwd.upstream.name
    pre_error: tuple[int, str] | None = None
    if not fwd.delivered:
        pre_error = (await _failure(fwd))[:2]

    async def events() -> AsyncIterator[bytes]:
        translator = StreamTranslator(requested, request_id)
        if pre_error is not None:
            status, detail = pre_error
            yield sse("error", {"type": "error", "error": {
                "type": "api_error" if status >= 500 else "invalid_request_error",
                "message": redact(detail, 400)}})
            log_call("POST", "/v1/messages", client, requested, upstream_model,
                     fwd.upstream.name if fwd.upstream else "-", status,
                     (time.perf_counter() - started) * 1000, error=detail)
            return

        failure = ""
        tail: list[bytes] = []
        try:
            lines = fwd.response.aiter_lines()
            # Read the first chunk before message_start so Claude Code gets
            # the real prompt-token count instead of a fabricated zero.
            first = None
            async for line in lines:
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    value = json.loads(chunk)
                except ValueError:
                    continue
                if isinstance(value, dict):
                    first = value
                    break
            input_tokens = int(((first or {}).get("usage") or {}).get("prompt_tokens") or 0)
            for event in translator.start(input_tokens):
                yield event
            if first is not None:
                for event in translator.handle(first):
                    yield event
            async for line in lines:
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if not chunk or chunk == "[DONE]":
                    continue
                try:
                    data = json.loads(chunk)
                except ValueError:
                    continue
                if not isinstance(data, dict):
                    continue
                if data.get("error"):
                    failure = f"upstream error chunk: {data['error']}"
                    yield sse("error", {"type": "error", "error": {
                        "type": "api_error", "message": redact(str(data["error"]), 400)}})
                    return
                for event in translator.handle(data):
                    yield event
            tail = translator.finish()
        except httpx.HTTPError as exc:
            failure = f"upstream stream failed: {type(exc).__name__}"
            yield sse("error", {"type": "error", "error": {
                "type": "api_error", "message": failure}})
            return
        finally:
            await _close(fwd)
            log_call("POST", "/v1/messages", client, requested, upstream_model,
                     fwd.upstream.name, 200 if not failure else 502,
                     (time.perf_counter() - started) * 1000,
                     prompt_tokens=translator.input_tokens or None,
                     completion_tokens=translator.output_tokens or None,
                     error=failure)
        for event in tail:
            yield event

    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    started = time.perf_counter()
    raw = await request.body()
    body = _peek(raw)
    if body is None:
        return JSONResponse(status_code=400, content={
            "error": {"type": "invalid_request_error",
                      "message": "request body must be a JSON object"}})
    requested = str(body.get("model") or "")
    upstream_model = config.route_model(requested)
    body["model"] = upstream_model
    stream = bool(body.get("stream"))
    client = _client_host(request)

    fwd = await _forward(_chat_targets(),
                         json.dumps(body, ensure_ascii=False).encode("utf-8"),
                         _relay_headers(request), stream=stream)
    if not fwd.ok:
        log_call("POST", "/v1/chat/completions", client, requested, upstream_model,
                 fwd.upstream.name if fwd.upstream else "-", fwd.status,
                 (time.perf_counter() - started) * 1000, error=fwd.error)
        headers = {"x-aqi-upstream": fwd.upstream.name} if fwd.upstream else None
        return JSONResponse(status_code=fwd.status, headers=headers,
                            content={"error": {"type": "upstream_error",
                                               "message": redact(fwd.error, 400)}})

    if not stream or fwd.status >= 400:
        response = await _buffered(fwd)
        prompt, completion = _usage(response.body)
        log_call("POST", "/v1/chat/completions", client, requested, upstream_model,
                 fwd.upstream.name, fwd.status, (time.perf_counter() - started) * 1000,
                 prompt_tokens=prompt, completion_tokens=completion)
        return response

    def done(failure: str) -> None:
        log_call("POST", "/v1/chat/completions", client, requested, upstream_model,
                 fwd.upstream.name, fwd.status, (time.perf_counter() - started) * 1000,
                 error=failure)

    return _relayed(fwd, done)


@app.post("/v1/responses")
async def responses(request: Request):
    started = time.perf_counter()
    raw = await request.body()
    # Codex puts provider-specific ids here that only the Responses upstream
    # understands, so this route relays untouched instead of routing the model.
    body = _peek(raw) or {}
    requested = str(body.get("model") or "")
    stream = bool(body.get("stream"))
    client = _client_host(request)

    fwd = await _forward(_responses_targets(), raw, _relay_headers(request), stream=stream)
    if not fwd.ok:
        log_call("POST", "/v1/responses", client, requested, requested,
                 fwd.upstream.name if fwd.upstream else "-", fwd.status,
                 (time.perf_counter() - started) * 1000, error=fwd.error)
        headers = {"x-aqi-upstream": fwd.upstream.name} if fwd.upstream else None
        return JSONResponse(status_code=fwd.status, headers=headers,
                            content={"error": {"type": "upstream_error",
                                               "message": redact(fwd.error, 400)}})

    if not stream or fwd.status >= 400:
        response = await _buffered(fwd)
        prompt, completion = _usage(response.body)
        log_call("POST", "/v1/responses", client, requested, requested,
                 fwd.upstream.name, fwd.status, (time.perf_counter() - started) * 1000,
                 prompt_tokens=prompt, completion_tokens=completion)
        return response

    def done(failure: str) -> None:
        log_call("POST", "/v1/responses", client, requested, requested,
                 fwd.upstream.name, fwd.status, (time.perf_counter() - started) * 1000,
                 error=failure)

    return _relayed(fwd, done)


if __name__ == "__main__":
    import os

    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("AQI_GATEWAY_PORT", "15722")))
