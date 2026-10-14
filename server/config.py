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
    "db_path": os.getenv("DB_PATH", str(DATA_DIR / "memory.db")),
    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
    "llm_base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "llm_api_key": os.getenv("LLM_API_KEY", ""),
    "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "llm_temperature": float(os.getenv("LLM_TEMPERATURE", "0.9")),
    "llm_max_tokens": int(os.getenv("LLM_MAX_TOKENS", "400")),
    # 对话用的 token 预算，和"回复该多短"是两件事。
    # 之前从字数上限反推（400字//2+120=320），推理模型会正好在 320 被截断，
    # 结果是 finish_reason=length + 回复只剩半个字符。
    "llm_reply_tokens": int(os.getenv("LLM_REPLY_TOKENS", "900")),
    "llm_reasoning_effort": os.getenv("LLM_REASONING_EFFORT", ""),
    # 重复抑制。0 = 不下发该字段。个别 OpenAI 兼容网关会因未知字段直接 400，
    # 所以默认关着，确认你的网关支持再在 .env 里开（对话建议 0.2 / 0.2）。
    "llm_frequency_penalty": float(os.getenv("LLM_FREQUENCY_PENALTY", "0")),
    "llm_presence_penalty": float(os.getenv("LLM_PRESENCE_PENALTY", "0")),
    # 记忆抽取单独一个模型。会演的模型适合对话但会把一句话拆成三条记忆，
    # 慢一点的反而更准；抽取在后台跑，慢一点不影响体验。
    "llm_extract_model": os.getenv("LLM_EXTRACT_MODEL", ""),
    "llm_extract_effort": os.getenv("LLM_EXTRACT_EFFORT", "low"),
    "asr_engine": os.getenv("ASR_ENGINE", "whisper"),
    "asr_model": os.getenv("ASR_MODEL", "small"),
    "asr_model_id": os.getenv("ASR_MODEL_ID", "stepaudio-2.5-asr"),
    # Explicit opt-in: never reuse subscription/LLM credentials for paid ASR.
    "asr_stream_url": os.getenv("ASR_STREAM_URL", ""),
    "asr_stream_api_key": os.getenv("ASR_STREAM_API_KEY", ""),
    "asr_stream_wire_format": os.getenv("ASR_STREAM_WIRE_FORMAT", "pcm"),
    "music_enabled": os.getenv("MUSIC_ENABLED", "false").lower() in ("1", "true", "yes"),
    "music_url": os.getenv("MUSIC_URL", ""),
    "music_api_key": os.getenv("MUSIC_API_KEY", ""),
    "asr_device": os.getenv("ASR_DEVICE", "cpu"),
    "asr_compute_type": os.getenv("ASR_COMPUTE_TYPE", "int8"),
    "asr_language": os.getenv("ASR_LANGUAGE", "zh"),
    "tts_engine": os.getenv("TTS_ENGINE", "edge_tts"),
    "tts_model": os.getenv("TTS_MODEL", "stepaudio-2.5-tts"),
    "tts_voice": os.getenv("TTS_VOICE", "livelybreezy-female"),
    "tts_format": os.getenv("TTS_FORMAT", "mp3"),
    "tts_sample_rate": int(os.getenv("TTS_SAMPLE_RATE", "24000")),
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