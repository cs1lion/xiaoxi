"""Conservative Chinese temporal-bound extraction for fact valid-time.

Only EXPLICIT range markers produce a bound. Single-point and open-ended
duration expressions -- 最近, 这段时间, 目前, 现在, a bare 这个月 -- are left
unresolved on purpose: a companion must not fabricate a deadline from an
undefined period (MEMORY_RESEARCH_NEXT §3: 「『最近』不能未经定义就编出具体
截止日；不确定时保留原表达与未解析状态」). An unresolved bound is None, which
the retrieval gate treats as "never expires" (valid-time increment 1).

Recognised markers (X/Y must resolve to a date; otherwise the bound is None):

    从X起 / 自从X        -> valid_from   (start of X's period)
    到X为止 / 直到X      -> valid_until  (end of X's period)
    从X到Y               -> both

Resolution is UTC epoch seconds and `now` is injected, so the resolver is
deterministic and testable; the caller passes time.time(). A from-bound uses
the period start, an until-bound the period end.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# A resolvable date expression: absolute (YYYY年M月D日 / M月 / 年) or a closed
# relative period. 现在/最近/目前/这段时间 are intentionally absent -- they are
# open-ended and must stay unresolved.
_DATE = (
    r'(?:\d{4}年\d{1,2}月\d{1,2}日?'
    r'|\d{4}年\d{1,2}月'
    r'|\d{4}年'
    r'|今天|昨天|明天|今年|去年|明年'
    r'|这个月|本月|上个月|下个月'
    r'|这周|本周|上周|下周)'
)
# 从X起 and 从X开始 both state an explicit start. Requiring 起 or 开始 immediately
# after the date drops the discourse marker 从X说起/讲起 (a 说/讲 sits between the
# date and 起). A residual discourse form, 从X开始说起, is accepted rather than
# guarded: a lookahead rejecting 说/讲 after 开始 would also kill the legitimate
# 从X开始说英语. A wrong fact_valid_from used to be inert -- the admission gates all
# read fact_valid_until only -- but since 2026-10-26 they read EITHER bound, so on a
# superseded row a wrong start date is now admitted and delivered under a time_note
# (a conservative one that claims neither validity nor withdrawal, so it cannot
# assert a wrong fact). /api/memories is still unaffected: to_dict pops both
# fact_valid_* fields. The only other place it surfaces is the optional markdown
# audit snapshot, which would show a wrong start date.
_FROM = re.compile(r'从(' + _DATE + r')(?:起|开始)|自从(' + _DATE + r')')
# 到X为止 carries a lookbehind so discourse markers -- 说到/讲到/谈到/提到/聊到
# X为止 ("speaking up to X") -- do not set a gating valid_until. 从X起 needs no
# such guard: requiring 起 immediately after the date already drops 从X说起/讲起.
_UNTIL = re.compile(r'(?<![说讲谈提聊])到(' + _DATE + r')为止|直到(' + _DATE + r')')
_RANGE = re.compile(r'从(' + _DATE + r')到(' + _DATE + r')')
# 从YYYY年M月[D日]到M月[D日]：第二界缺年份，承继首界年份。只在**首界显式带年份**
# 时成立——裸「3月到6月」没有年份上下文，不予猜测（保守）。
_RANGE_INHERIT = re.compile(
    r'从(\d{4})年(\d{1,2})月(?:(\d{1,2})日?)?到(\d{1,2})月(?:(\d{1,2})日?)?')

_DAY_END = {"hour": 23, "minute": 59, "second": 59}


def _month_end(year: int, month: int) -> datetime:
    if month == 12:
        return datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    return datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(seconds=1)


def _day_bounds(day: "datetime.date") -> tuple[datetime, datetime]:
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    return start, start.replace(**_DAY_END)


def _epoch(moment: datetime) -> float | None:
    """Epoch seconds, or None when it predates the store's nonnegative contract.

    ``store.upsert`` rejects a negative bound, so resolving ``从1969年起`` to a
    negative epoch would raise on the write path -- and in the extraction loop a
    single such candidate would drop every other memory in that turn. Treat it as
    unresolvable instead, which is the conservative side.
    """
    seconds = moment.timestamp()
    return seconds if seconds >= 0 else None


def _resolve(expr: str, *, is_end: bool, now_dt: datetime) -> float | None:
    """Epoch seconds for one date expression, or None if it does not resolve.

    Absolute dates are validated; a relative period is measured from `now_dt`.
    An unknown or malformed expression returns None (the safe side).
    """
    m = re.fullmatch(r'(\d{4})年(\d{1,2})月(\d{1,2})日?', expr)
    if m:
        try:
            day = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
        except ValueError:
            return None
        return _epoch(day.replace(**_DAY_END) if is_end else day)
    m = re.fullmatch(r'(\d{4})年(\d{1,2})月', expr)
    if m:
        year, month = int(m[1]), int(m[2])
        if not 1 <= month <= 12:
            return None
        moment = (_month_end(year, month) if is_end
                  else datetime(year, month, 1, tzinfo=timezone.utc))
        return _epoch(moment)
    m = re.fullmatch(r'(\d{4})年', expr)
    if m:
        year = int(m[1])
        moment = (datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc) if is_end
                  else datetime(year, 1, 1, tzinfo=timezone.utc))
        return _epoch(moment)

    y, mo = now_dt.year, now_dt.month
    if expr in ("这个月", "本月"):
        start, end = datetime(y, mo, 1, tzinfo=timezone.utc), _month_end(y, mo)
    elif expr == "上个月":
        py, pm = (y - 1, 12) if mo == 1 else (y, mo - 1)
        start, end = datetime(py, pm, 1, tzinfo=timezone.utc), _month_end(py, pm)
    elif expr == "下个月":
        ny, nm = (y + 1, 1) if mo == 12 else (y, mo + 1)
        start, end = datetime(ny, nm, 1, tzinfo=timezone.utc), _month_end(ny, nm)
    elif expr == "今年":
        start = datetime(y, 1, 1, tzinfo=timezone.utc)
        end = datetime(y, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    elif expr == "去年":
        start = datetime(y - 1, 1, 1, tzinfo=timezone.utc)
        end = datetime(y - 1, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    elif expr == "明年":
        start = datetime(y + 1, 1, 1, tzinfo=timezone.utc)
        end = datetime(y + 1, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
    elif expr == "今天":
        start, end = _day_bounds(now_dt.date())
    elif expr == "昨天":
        start, end = _day_bounds((now_dt - timedelta(days=1)).date())
    elif expr == "明天":
        start, end = _day_bounds((now_dt + timedelta(days=1)).date())
    elif expr in ("这周", "本周", "上周", "下周"):
        offset = {"这周": 0, "本周": 0, "上周": 7, "下周": -7}[expr]
        monday = (now_dt - timedelta(days=now_dt.weekday() + offset)).date()
        start = datetime(monday.year, monday.month, monday.day, tzinfo=timezone.utc)
        end = start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    else:
        return None
    return (end if is_end else start).timestamp()


def temporal_bounds(text: str, *, now: float) -> tuple[float | None, float | None]:
    """Return (valid_from, valid_until) epoch seconds from explicit range markers.

    Conservative: no marker, an unresolvable date, or an open-ended duration
    expression yields (None, None). A 从X到Y range wins over the separate
    from/until scans. A from-bound uses the period start; an until-bound the
    period end.
    """
    if not isinstance(text, str) or not text:
        return (None, None)
    now_dt = datetime.fromtimestamp(now, tz=timezone.utc)
    valid_from = valid_until = None
    inherit = _RANGE_INHERIT.search(text)
    rng = _RANGE.search(text)
    # Leftmost range wins: an inherit-shaped range must not preempt an earlier
    # general one merely because it is the only one of its kind.
    if inherit and (rng is None or inherit.start() <= rng.start()):
        # 从2024年3月到6月：第二界承继首界年份。越界月/日仍由 _resolve 的
        # ValueError 路径落回 None，进而使整条区间判为未解析（见下方检查）。
        year = int(inherit[1])
        valid_from = _resolve(f"{year}年{inherit[2]}月{inherit[3] or ''}",
                              is_end=False, now_dt=now_dt)
        valid_until = _resolve(f"{year}年{inherit[4]}月{inherit[5] or ''}",
                               is_end=True, now_dt=now_dt)
    elif rng:
        valid_from = _resolve(rng[1], is_end=False, now_dt=now_dt)
        valid_until = _resolve(rng[2], is_end=True, now_dt=now_dt)
    else:
        frm = _FROM.search(text)
        if frm:
            valid_from = _resolve(frm[1] or frm[2], is_end=False, now_dt=now_dt)
        unt = _UNTIL.search(text)
        if unt:
            valid_until = _resolve(unt[1] or unt[2], is_end=True, now_dt=now_dt)
    # A range needs BOTH bounds. One unresolvable bound makes the whole range
    # unresolved instead of a half-range that would read as a deliberate
    # single-sided bound. One-sided 从X起 / 到X为止 markers are unaffected.
    if (inherit or rng) and (valid_from is None or valid_until is None):
        return (None, None)
    # A contradictory range (start after end) stays unresolved rather than
    # becoming a bound that would retire the row immediately -- the safe side
    # for the retrieval gate.
    if valid_from is not None and valid_until is not None and valid_from > valid_until:
        return (None, None)
    return (valid_from, valid_until)
