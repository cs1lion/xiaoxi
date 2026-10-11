from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = ROOT / "config"
DATA_DIR = ROOT / "data"
AUDIO_DIR = DATA_DIR / "audio"

load_dotenv(ROOT / ".env")

# HuggingFace is unreachable on some networks; the mirror + plain CDN works.
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    with path.open("r", encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


@lru_cache(maxsize=1)
def persona_cfg() -> dict[str, Any]:
    return _load_yaml("persona.yaml")


@lru_cache(maxsize=1)
def memory_cfg() -> dict[str, Any]:
    return _load_yaml("memory.yaml")


@lru_cache(maxsize=1)
def pacing_cfg() -> dict[str, Any]:
    return _load_yaml("pacing.yaml")


@lru_cache(maxsize=1)
def avatar_cfg() -> dict[str, Any]:
    return _load_yaml("avatar.yaml")


DEFAULTS: dict[str, Any] = {
    "db_path": str(DATA_DIR / "memory.db"),
    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
    "llm_base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "llm_api_key": os.getenv("LLM_API_KEY", ""),
    "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "llm_temperature": float(os.getenv("LLM_TEMPERATURE", "0.9")),
    "llm_max_tokens": int(os.getenv("LLM_MAX_TOKENS", "400")),
    "asr_model": os.getenv("ASR_MODEL", "small"),
    "asr_device": os.getenv("ASR_DEVICE", "cpu"),
    "asr_compute_type": os.getenv("ASR_COMPUTE_TYPE", "int8"),
    "asr_language": os.getenv("ASR_LANGUAGE", "zh"),
    "tts_engine": os.getenv("TTS_ENGINE", "edge_tts"),
    "tts_default_emotion": os.getenv("TTS_DEFAULT_EMOTION", "base"),
    "audio_sample_rate": 16000,
    "audio_channels": 1,
    "port": int(os.getenv("PORT", "8000")),
    "host": os.getenv("HOST", "127.0.0.1"),
}


class Settings:
    """Simple attribute bag; keys come from DEFAULTS and can be overridden by env."""

    def __init__(self, overrides: dict[str, Any] | None = None) -> None:
        for key, value in DEFAULTS.items():
            setattr(self, key, value)
        for key, value in (overrides or {}).items():
            setattr(self, key, value)

    def as_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in DEFAULTS}


settings = Settings()


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)