"""Gateway configuration and model routing, parsed once from the environment.

Environment variables are a system boundary: everything is parsed here into a
frozen `Settings`, and the rest of the gateway reads typed values only. A bad
`AQI_GATEWAY_ROUTES` degrades to keyword routing instead of taking the gateway
down, because the workers depend on this port being up.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Mapping

logger = logging.getLogger("aqi.gateway")

DEFAULT_UPSTREAM = "http://127.0.0.1:15721/v1"
DEFAULT_RESPONSES_UPSTREAM = "http://127.0.0.1:15721/v1/responses"
DEFAULT_MODEL = "step-5-preview"
DEFAULT_CHEAP_MODEL = "step-3.7-flash"

# Inbound ids carry their own vendor names (`claude-haiku-4-5`,
# `deepseek-flash`, `*-lite`), so the cheap tier is matched by substring rather
# than by an exhaustive alias list.
CHEAP_HINTS = ("flash", "haiku", "lite")


@dataclass(frozen=True)
class Upstream:
    name: str
    base_url: str
    key: str

    def headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.key}",
            "Content-Type": "application/json",
            # Keep relayed SSE bytes identical to what the upstream produced.
            "Accept-Encoding": "identity",
        }


@dataclass(frozen=True)
class Settings:
    upstream: Upstream
    fallback: Upstream | None
    responses_upstream: str
    model: str
    cheap_model: str
    routes: Mapping[str, str]
    max_output: int
    timeout: float

    @property
    def upstreams(self) -> tuple[Upstream, ...]:
        if self.fallback is None:
            return (self.upstream,)
        return (self.upstream, self.fallback)

    def route(self, requested: str | None) -> str:
        return route_model(requested, routes=self.routes, main=self.model,
                           cheap=self.cheap_model)


def route_model(requested: str | None, *, routes: Mapping[str, str] | None = None,
                main: str | None = None, cheap: str | None = None) -> str:
    """Explicit table first, then the cheap-tier keyword, then the main model."""
    name = str(requested or "").strip()
    table = SETTINGS.routes if routes is None else routes
    if name in table:
        return table[name]
    if any(hint in name.lower() for hint in CHEAP_HINTS):
        return SETTINGS.cheap_model if cheap is None else cheap
    return SETTINGS.model if main is None else main


def _routes_from_env(raw: str) -> Mapping[str, str]:
    if not raw.strip():
        return {}
    try:
        table = json.loads(raw)
    except ValueError:
        logger.warning("AQI_GATEWAY_ROUTES is not valid JSON; using keyword routing only")
        return {}
    if not isinstance(table, dict):
        logger.warning("AQI_GATEWAY_ROUTES must be a JSON object; using keyword routing only")
        return {}
    return {str(key): str(value) for key, value in table.items() if str(value).strip()}


def from_env() -> Settings:
    base = os.environ.get("AQI_GATEWAY_UPSTREAM", DEFAULT_UPSTREAM).strip().rstrip("/")
    key = os.environ.get("AQI_GATEWAY_KEY", "PROXY_MANAGED")
    fallback_base = os.environ.get("AQI_GATEWAY_FALLBACK_UPSTREAM", "").strip().rstrip("/")
    fallback = None
    if fallback_base:
        fallback = Upstream("fallback", fallback_base,
                            os.environ.get("AQI_GATEWAY_FALLBACK_KEY") or key)
    return Settings(
        upstream=Upstream("primary", base, key),
        fallback=fallback,
        responses_upstream=os.environ.get(
            "AQI_GATEWAY_RESPONSES_UPSTREAM", DEFAULT_RESPONSES_UPSTREAM).strip(),
        model=os.environ.get("AQI_GATEWAY_MODEL", DEFAULT_MODEL),
        cheap_model=os.environ.get("AQI_GATEWAY_CHEAP_MODEL", DEFAULT_CHEAP_MODEL),
        routes=_routes_from_env(os.environ.get("AQI_GATEWAY_ROUTES", "")),
        max_output=int(os.environ.get("AQI_GATEWAY_MAX_TOKENS", "16384")),
        timeout=float(os.environ.get("AQI_GATEWAY_TIMEOUT", "600")),
    )


SETTINGS = from_env()
MODEL = SETTINGS.model
MAX_OUTPUT = SETTINGS.max_output
TIMEOUT = SETTINGS.timeout
