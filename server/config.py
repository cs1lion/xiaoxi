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


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge; override wins. Non-dict override replaces wholesale."""
    result = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def _live(name: str, base_loader: Any) -> dict[str, Any]:
    """Return the hot snapshot when a local overlay is active, else the base.

    With no overlay the lru-cached base is returned, so behavior is byte-identical
    to a plain startup load until config_hot installs a snapshot.
    """
    from . import config_hot  # lazy: avoids an import cycle at module load
    snapshot = config_hot.snapshot(name)
    return base_loader() if snapshot is None else snapshot


@lru_cache(maxsize=1)
def _persona_base() -> dict[str, Any]:
    return _load_yaml("persona.yaml")


def persona_cfg() -> dict[str, Any]:
    return _live("persona.yaml", _persona_base)


@lru_cache(maxsize=1)
def _memory_base() -> dict[str, Any]:
    return _load_yaml("memory.yaml")


def memory_cfg() -> dict[str, Any]:
    return _live("memory.yaml", _memory_base)


@lru_cache(maxsize=1)
def _pacing_base() -> dict[str, Any]:
    return _load_yaml("pacing.yaml")


def pacing_cfg() -> dict[str, Any]:
    return _live("pacing.yaml", _pacing_base)


@lru_cache(maxsize=1)
def _avatar_base() -> dict[str, Any]:
    return _load_yaml("avatar.yaml")


@lru_cache(maxsize=1)
def _agents_roster_base() -> dict[str, Any]:
    return _load_yaml("agents_roster.yaml")


def agents_roster_cfg() -> dict[str, Any]:
    """V3 麾下花名册。与 persona/pacing 一样走 _live，因此 config_hot 装上快照后
    改 agents_roster.yaml 无需重启即可在下次派单生效（协议 §17）。"""
    return _live("agents_roster.yaml", _agents_roster_base)


def avatar_cfg() -> dict[str, Any]:
    return _live("avatar.yaml", _avatar_base)


@lru_cache(maxsize=1)
def _seeds_base() -> dict[str, Any]:
    return _load_yaml("seeds.yaml")


def seeds_cfg() -> dict[str, Any]:
    """初始好奇心种子池（协议 §14.1）。

    协议 §17 的覆盖范围里写着 `seeds.yaml`，但此前它既不在 config_hot 的
    `MANAGED` 里、读取方也绕开了访问器（`seeds.load_initial_seeds()` 直接读盘），
    于是 `config/seeds.local.yaml` 写了也不生效，且没有任何日志。这里补上访问器，
    读取方改走它之后覆盖才真正可用。
    """
    return _live("seeds.yaml", _seeds_base)


DEFAULTS: dict[str, Any] = {
    "db_path": os.getenv("DB_PATH", str(DATA_DIR / "memory.db")),
    "llm_provider": os.getenv("LLM_PROVIDER", "openai"),
    "llm_base_url": os.getenv("LLM_BASE_URL", "https://api.deepseek.com/v1"),
    "llm_api_key": os.getenv("LLM_API_KEY", ""),
    # 内部端点（/internal/*）的共享令牌。空 = 不校验（保持旧行为）。
    # 设了之后每个 /internal/* 请求要带 X-Internal-Token 且相等，否则 401。
    # 这是为 /internal/telemetry_proxy 加的：它拿 llm_api_key 转发任意 path，
    # 原先只查 Origin 头，不带 Origin 的本机进程一条 curl 就能白嫖额度。
    "internal_token": os.getenv("INTERNAL_TOKEN", ""),
    "llm_model": os.getenv("LLM_MODEL", "deepseek-chat"),
    "llm_temperature": float(os.getenv("LLM_TEMPERATURE", "0.9")),
    "llm_max_tokens": int(os.getenv("LLM_MAX_TOKENS", "400")),
    # 对话用的 token 预算，和"回复该多短"是两件事。
    # 之前从字数上限反推（400字//2+120=320），推理模型会正好在 320 被截断，
    # 结果是 finish_reason=length + 回复只剩半个字符。
    # 900 仍不够：2026-10-17 实测 18 轮里 1 次推理占满全部 900（正文为空），
    # 同一组对话把上限提到 1200 后 18 轮 0 次截断。上限只是封顶，不改变正常回复的用量。
    "llm_reply_tokens": int(os.getenv("LLM_REPLY_TOKENS", "1200")),
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
    # 供应商在每个音频块前后各垫了约 0.36s / 0.38s 静音。微句链路把这段静音和
    # pacing.yaml 里设计的呼吸停顿叠加起来，实际句间空隙达到 0.9~1.1s，人听起来
    # 是一顿一顿的。打开时按 WAV 修剪首尾静音，让设计的 pause 成为真实停顿。
    "tts_trim_silence": os.getenv("TTS_TRIM_SILENCE", "true").lower() in ("1", "true", "yes"),
    # 修剪阈值（峰值幅度 0~1）与保留的边缘静音量（秒）。保留一点点边缘比切到
    # 零更好听：硬切会把起音/收音的毛刺一起暴露出来。
    "tts_trim_threshold": float(os.getenv("TTS_TRIM_THRESHOLD", "0.012")),
    "tts_trim_pad_ms": int(os.getenv("TTS_TRIM_PAD_MS", "60")),
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
