"""Finite, evidence-derived preference members; no storage or model authority.

This recognizer deliberately understands only a small set of Chinese direct
self-report clauses. Unknown wording keeps the ordinary conservative memory
path. It does not infer aliases, calendar validity, or a user's unstated ranking.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


class PreferenceValidationError(ValueError):
    """An explicit structured candidate is invalid or unsupported by evidence."""


LEVELS = frozenset(("like", "favorite", "dislike"))
SCOPES = frozenset(("long_term", "recent", "current", "unspecified"))
_TIME = r"长期|一直|最近|近来|这阵子|这段时间|现在|目前|如今"
_SUBJECT = r"我|本人|用户"
_CUE = r"不再喜欢|已经不喜欢|早就不喜欢|不喜欢|最喜欢|最爱|喜欢|喜爱"
_VERB = r"喝|吃|玩|看|听"
_TIME_SCOPE = {
    "长期": "long_term", "一直": "long_term",
    "最近": "recent", "近来": "recent", "这阵子": "recent", "这段时间": "recent",
    "现在": "current", "目前": "current", "如今": "current",
}
_FORWARD = re.compile(
    rf"^(?P<subject>{_SUBJECT})(?P<time>{_TIME})?(?:也)?(?:很|挺|比较)?"
    rf"(?P<cue>{_CUE})(?:{_VERB})?(?P<target>.+?)(?:了|的)?$"
)
_INVERTED = re.compile(
    rf"^(?P<subject>{_SUBJECT})?(?P<time>{_TIME})?(?P<target>.+?)"
    rf"(?P<tail_subject>我)?也(?:很|挺|比较)?(?P<cue>{_CUE})(?:的|了)?$"
)
_FAVORITE_CHANGE = re.compile(
    rf"^(?P<subject>{_SUBJECT})(?P<time>{_TIME})?(?:把)?(?:最喜欢|最爱)"
    rf"(?:(?:{_VERB})?的(?:饮品|茶|食物|点心|游戏|电影|音乐|歌曲|运动|东西|对象))?"
    r"(?:已经)?(?:改为|改成|换为|换成)(?P<target>.+?)(?:了)?$"
)
_CLAUSE = re.compile(r"[^，,。；;！!？?\n]+")
# These are finite omission gates, not a general language parser. In particular,
# conjunctions in an object are omitted rather than guessed into separate members.
_UNSUPPORTED_TARGET = re.compile(
    r"[和与及跟或、/\\]|以及|或者|还是|等|都|不|没|喜欢|最爱|决定|习惯|改|"
    r"可能|也|但是|不过|其实|说|最近|近来|长期|一直|现在|目前|如今|"
    r"以前|过去|曾经|将来|以后|平时|通常|偶尔|今天|昨天|明天|[\"'“”‘’「」『』:：]"
)
_PREFERENCE_CUE = re.compile(r"喜欢|喜爱|最爱")
_SOURCE_EXCLUSION = re.compile(
    r"假设|假如|如果|要是|假装|虚构|扮演|台词|[\"“”「」『』]|"
    r"(?:他|她|他们|她们)(?:说|喜欢|打算|是|在|的)|"
    r"(?:我|我的)(?:朋友|同事|同学|家人|老板|父母|爸爸|妈妈|哥哥|姐姐|弟弟|妹妹)"
)
_NO_CHANGE = re.compile(
    r"没(?:有)?决定|还未决定|尚未决定|不(?:打算|准备|想|会|要)(?:.{0,12})(?:改|换)|"
    r"不改|不换|没有改|没改|没有换|没换|要不要|是否|并非|不是|未必"
)
_CORRECTION = re.compile(r"^(?:纠正|更正)(?:一下)?$|^(?:我)?(?:说错|记错)了?$")
PREFERENCE_KEYS = frozenset((
    "饮食:饮品偏好", "饮食:食物偏好", "饮食:偏好", "游戏:偏好",
    "影视:偏好", "音乐:偏好", "运动:偏好", "兴趣:偏好",
))


@dataclass(frozen=True)
class _Assertion:
    target: str
    level: str
    scope: str
    changed: bool = False

    def fields(self) -> dict[str, str]:
        return {"preference_target": self.target, "preference_level": self.level,
                "preference_scope": self.scope}


def _parse_clause(clause: str, *, allow_elliptical: bool,
                  allow_user_subject: bool = False) -> _Assertion | None:
    clause = clause.strip()
    match = _FAVORITE_CHANGE.fullmatch(clause)
    changed = match is not None
    if match is None:
        match = _FORWARD.fullmatch(clause)
    if match is None:
        match = _INVERTED.fullmatch(clause)
        if match is not None and not match.group("subject") and not match.group("tail_subject"):
            # The recorded recent-tea supplement is the only deliberately
            # supported subject omission. Arbitrary nouns such as website users
            # are not evidence that the speaker described their own preference.
            if (not allow_elliptical or match.group("target").strip() not in ("红茶", "绿茶")
                    or match.group("time") not in ("最近", "近来", "这阵子", "这段时间")
                    or match.group("cue") not in ("喜欢", "喜爱")):
                return None
    if match is None:
        return None
    if match.group("subject") == "用户" and not allow_user_subject:
        return None
    target = match.group("target").strip()
    if (not target or len(target) > 40 or _UNSUPPORTED_TARGET.search(target)
            or not re.fullmatch(r"[\w\-\u3400-\u9fff]+", target)):
        return None
    cue = "最喜欢" if changed else match.group("cue")
    level = ("dislike" if cue in ("不喜欢", "不再喜欢", "已经不喜欢", "早就不喜欢")
             else "favorite" if cue in ("最喜欢", "最爱") else "like")
    return _Assertion(target, level, _TIME_SCOPE.get(match.group("time"), "unspecified"),
                      changed or cue == "不再喜欢")


def _assertions(text: str, *, allow_elliptical: bool,
                allow_user_subject: bool = False) -> list[tuple[_Assertion, int, int]] | None:
    found: list[tuple[_Assertion, int, int]] = []
    for match in _CLAUSE.finditer(text):
        raw_clause = match.group()
        clause = raw_clause.strip()
        assertion = _parse_clause(clause, allow_elliptical=allow_elliptical,
                                  allow_user_subject=allow_user_subject)
        if assertion is not None:
            start = match.start() + len(raw_clause) - len(raw_clause.lstrip())
            found.append((assertion, start, start + len(clause)))
        elif _PREFERENCE_CUE.search(clause):
            # Do not extract a positive span from a negated, plural, uncertain,
            # or otherwise unsupported preference clause.
            return None
    return found


def normalize_preference(
    raw_optional: Any, quote: str, value: str, key: str, kind: str, subject: str, source: str,
    *, message_text: str | None = None,
) -> dict[str, str] | None:
    """Return only fields re-derived from a supported complete user assertion.

    ``None`` means absent/unsupported ordinary metadata. Explicit structures must
    contain exactly target/level/scope and agree with both original evidence and
    the stored statement; otherwise they raise. Callers must reject explicit
    JSON null before this function (None denotes an absent optional field).
    Writing callers pass the saved full message. Without it, only complete
    explicit self-report clauses can be recognized; no occurrence is invented.
    No timestamp, identity slot, status, or replacement permission is returned.
    """
    explicit = raw_optional is not None

    def unsupported(reason: str) -> None:
        if explicit:
            raise PreferenceValidationError(reason)
        return None

    if explicit:
        if (not isinstance(raw_optional, dict) or set(raw_optional) != {"target", "level", "scope"}
                or not isinstance(raw_optional.get("target"), str)
                or not isinstance(raw_optional.get("level"), str)
                or not isinstance(raw_optional.get("scope"), str)
                or raw_optional.get("level") not in LEVELS or raw_optional.get("scope") not in SCOPES):
            raise PreferenceValidationError("preference 必须仅含有效 target/level/scope")
    if (kind != "fact" or subject != "user" or source != "stated"
            or not isinstance(key, str) or key not in PREFERENCE_KEYS
            or not isinstance(quote, str) or not isinstance(value, str)
            or not quote.strip() or not value.strip()):
        return unsupported("preference 只准入带原话的 stated user fact 偏好属性")
    full = quote if message_text is None else message_text
    if (not isinstance(full, str) or full.count(quote) != 1
            or _SOURCE_EXCLUSION.search(full)):
        return unsupported("preference 原话来源不直接或出现位置不唯一")
    quoted = _assertions(quote, allow_elliptical=message_text is not None)
    if quoted is None or len(quoted) != 1:
        return unsupported("preference 原话不是受支持的单对象偏好断言")
    original = _assertions(full, allow_elliptical=message_text is not None)
    offset = full.find(quote)
    covered = [] if original is None else [item for item in original
                                           if offset <= item[1] and item[2] <= offset + len(quote)]
    if len(covered) != 1 or covered[0][0] != quoted[0][0]:
        return unsupported("preference 证据截掉完整分句或限定词")
    statement = _assertions(value, allow_elliptical=False, allow_user_subject=True)
    expected = quoted[0][0]
    if (statement is None or len(statement) != 1
            or (statement[0][0].target, statement[0][0].level, statement[0][0].scope)
            != (expected.target, expected.level, expected.scope)):
        return unsupported("preference value 与原话对象、程度或时间范围不符")
    if explicit and raw_optional != {"target": expected.target, "level": expected.level,
                                     "scope": expected.scope}:
        raise PreferenceValidationError("preference 模型标签与原话不一致")
    return expected.fields()


def preference_change_allowed(quote: str, *, level: str,
                              message_text: str | None = None) -> bool:
    """Recognize a narrow local change assertion, never a model update flag.

    Ordinary likes cannot replace other members. Favorites need an explicit
    change/correction clause, dislikes need an explicit no-longer-like clause.
    Pending/negated changes anywhere in the complete saved message conservatively
    disable both routes, even when the evidence quote omits that later clause.
    """
    full = quote if message_text is None else message_text
    if (not isinstance(quote, str) or level not in ("favorite", "dislike")
            or not isinstance(full, str) or full.count(quote) != 1 or _NO_CHANGE.search(full)):
        return False
    found = _assertions(quote, allow_elliptical=False)
    if found is None or len(found) != 1 or found[0][0].level != level:
        return False
    assertion, start, _ = found[0]
    if assertion.changed:
        return True
    if level == "favorite":
        preceding = [m.group().strip() for m in _CLAUSE.finditer(quote[:start])]
        return bool(preceding and _CORRECTION.fullmatch(preceding[-1]))
    return False
