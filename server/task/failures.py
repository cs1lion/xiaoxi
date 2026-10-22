"""失败分类、签名归一化与默认去向（规范 §4.6）。

签名的作用是回答「这是不是同一个错」：同一种错误第二次出现就不该再试。
所以归一化必须先把只会变化、不携带语义的部分抹掉（时间戳、随机 id、数字、路径前缀），
再按「类 + 参数键 + 错误串」拼成稳定字符串。

纯字符串处理：不读文件、不打日志、不抛业务异常以外的副作用。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# §4.6 八类失败
FAILURE_CLASSES: tuple[str, ...] = (
    "E_SPEC",
    "E_INPUT",
    "E_TOOL",
    "E_CRITERION",
    "E_BUDGET",
    "E_PERMISSION",
    "E_EFFECT",
    "E_AMBIGUOUS",
)

# 只有工具错和断言不过可以自动重试；其余要么改契约、要么找人。
RETRYABLE: frozenset[str] = frozenset({"E_TOOL", "E_CRITERION"})

# §4.6 默认去向（逐字）
DEFAULT_DESTINATION: dict[str, str] = {
    "E_SPEC": "PROPOSED",
    "E_INPUT": "BLOCKED",
    "E_TOOL": "REJECTED",
    "E_CRITERION": "EXECUTING",
    "E_BUDGET": "FAILED",
    "E_PERMISSION": "BLOCKED",
    "E_EFFECT": "FAILED",
    "E_AMBIGUOUS": "UNKNOWN",
}

# 归一化顺序敏感，四组正则按 §4.6 的先后出场。
# 注意：文本先小写，"T" / "Z" 在这里必须按小写写，否则时间戳永远匹配不上。
_TIMESTAMP = re.compile(
    r"\d{4}-\d{2}-\d{2}[t ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:z|[+-]\d{2}:?\d{2})?"
    r"|\d{4}/\d{2}/\d{2}[ t]\d{2}:\d{2}:\d{2}"
)
_UUID = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"
    r"|\b[0-9a-f]{32,}\b"
)
_NUMBER = re.compile(r"\b\d+\b")
_PATH_TOKEN = re.compile(r"[^\s]*[\\/][^\s]*")
_WHITESPACE = re.compile(r"\s+")

_TRIM_HEAD = "\"'([{<"
_TRIM_TAIL = "\"')]}>.,;:"


def _path_token(match: "re.Match[str]") -> str:
    """把一段路径压成 <path:basename>，保留外层引号与标点。"""
    raw = match.group(0)
    head = ""
    tail = ""
    token = raw
    while token and token[0] in _TRIM_HEAD:
        head += token[0]
        token = token[1:]
    while token and token[-1] in _TRIM_TAIL:
        tail = token[-1] + tail
        token = token[:-1]
    parts = [part for part in re.split(r"[\\/]+", token) if part]
    if len(parts) < 2:
        return raw
    return f"{head}<path:{parts[-1]}>{tail}"


def normalize_error_text(text: str) -> str:
    """按 §4.6 的固定顺序归一化错误串。"""
    value = (text or "").lower()
    value = _TIMESTAMP.sub("<ts>", value)
    value = _UUID.sub("<id>", value)
    value = _NUMBER.sub("<n>", value)
    value = _PATH_TOKEN.sub(_path_token, value)
    value = _WHITESPACE.sub(" ", value).strip()
    return value


@dataclass(frozen=True)
class Failure:
    """一次结构化的失败；重试时必须原样带回下一轮派单密件。"""

    failure_class: str
    signature: str
    evidence: str
    retryable: bool

    def to_feedback(self) -> dict:
        """给 BriefingPacket.feedback 的三件套：类、签名、证据。"""
        return {
            "class": self.failure_class,
            "signature": self.signature,
            "evidence": self.evidence,
        }


def build_signature(
    failure_class: str,
    parts: dict[str, str],
    *,
    free_text: str = "",
) -> str:
    """拼签名：类 + 按键名排序的参数 + 归一化后的自由文本。"""
    if failure_class not in FAILURE_CLASSES:
        raise ValueError(f"未知的失败类别：{failure_class!r}")
    segments = [failure_class]
    for key in sorted((parts or {}).keys()):
        # parts 是结构化字段（step_id / 工具名），原样保留；只有自由文本走归一化。
        segments.append(f"{key}={str((parts or {}).get(key, '')).strip()}")
    if free_text:
        segments.append(f"err={normalize_error_text(free_text)}")
    return "|".join(segments)


def classify(
    failure_class: str,
    parts: dict[str, str] | None = None,
    *,
    free_text: str = "",
    evidence: str = "",
) -> Failure:
    """按类别建一条失败记录；未知类别直接 ValueError。"""
    if failure_class not in FAILURE_CLASSES:
        raise ValueError(f"未知的失败类别：{failure_class!r}")
    return Failure(
        failure_class=failure_class,
        signature=build_signature(failure_class, parts or {}, free_text=free_text),
        evidence=evidence,
        retryable=failure_class in RETRYABLE,
    )


def destination_for(failure_class: str) -> str:
    """该类失败的默认去向（§4.6）。"""
    if failure_class not in DEFAULT_DESTINATION:
        raise ValueError(f"未知的失败类别：{failure_class!r}")
    return DEFAULT_DESTINATION[failure_class]
