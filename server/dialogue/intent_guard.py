"""Suspicion layer for the fast voice loop.

Why this exists
---------------
The fast loop decides intent with regexes (`orchestrator._DELEGATION` and
friends). Synonyms the patterns do not enumerate therefore fall through to
ordinary dialogue. That would be harmless, except the persona is *instructed*
to announce acceptance on any delegation (`persona.CHIEF_OF_STAFF_BLOCK`:
接到委托只说一句"接单了"). Routing says no, the prompt says claim it -- so the
assistant answers 接单了 for a commission that was never created.

Measured on the real model (step-3.7-flash, production prompts): 20 of 36
samples of 12 paraphrased delegations claimed acceptance, with zero ledger
entries behind them. With this constraint injected: 0 of 36.

What this module is NOT
-----------------------
It never decides routing, never touches the ledger, never calls a model, and
never adds UI. `executive_route()` and `should_route()` keep their exact
current return values; only the wording of an ordinary reply is constrained.

Purity requirement
------------------
`guard_for()` MUST stay a pure function of its argument. `_render_turn_context`
is used as the `measure` callback of `limit_context` and is therefore called
repeatedly while pruning (orchestrator._turn_context). Anything stateful here
(per-session throttling, counters, logging) would fire many times per turn.
Callers that need throttling or tracing do it once, around the call.
"""
from __future__ import annotations

import re

# Wider than orchestrator._DELEGATION on purpose. This layer only needs to spot
# "this sounds like work being handed over", not to prove it precisely: a false
# positive costs one extra sentence of clarification, a false negative costs a
# fabricated acceptance.
_DELEGATION_SUSPECT = re.compile(
    r'(?:帮我|替我|帮忙|麻烦|劳驾|请)(?:把|给|弄|搞|做|跑|写|改|整理|查|生成|安排|处理|看|清|收拾)'
    r'|(?:把|将)[^，。！？]{1,20}(?:弄|搞|做|改|生成|整理|清|跑|写|安排|处理|落实|派|交|发)'
    r'(?:一下|一遍|一版|个|下)?'
    r'|(?:派|交|丢|扔|托|委托|安排|分派|下发|布置)(?:下去|出去|给人|个人|一单|下去吧|一下)'
    r'|(?:找人|叫个人|让人|让谁|找谁|安排人|安排个人)'
    r'|(?:这|那)(?:活儿|活|事|事儿|单|单子)[^，。！？]{0,10}(?:得|要|该|谁|交给|派|弄|干|做)'
    r'|(?:落实|执行|推进|跟进|盯)(?:下去|一下|着)'
)

_PROGRESS_SUSPECT = re.compile(
    r'(?:到哪(?:一)?步|到哪了|什么阶段|什么情况了|哪一步了|进展|如何了|还要多久'
    r'|多久能|快好了|搞到哪|办得咋样|办得怎么样|弄到哪'
    # "报一下进度" / "把进度说一下" 这类把「进度」放宾语的问法
    r'|(?:进度|情况|状态)[^，。！？]{0,6}(?:报|说|讲|同步|回|查|看|更新)'
    r'|(?:报|说|讲|同步|查|看|更新)[^，。！？]{0,4}(?:进度|情况|状态))'
)

# Wording is measured, not guessed. 12 paraphrased delegations x 3 runs each
# (step-3.7-flash, production prompts):
#   no constraint           -> 20/36 claimed acceptance (56%)
#   earlier, shorter phrase -> 1/33
#   this phrase             -> 0/36
# The earlier phrase only banned "接单了"; the model routed around it with
# "我让Worker去跑". This one names the phrasings that actually leaked, and says
# there is no Worker running either -- the model was inferring one from the
# persona block ("交给你麾下的 Worker").
DELEGATION_GUARD = (
    '这一轮你手上没有真的新建任何工作单，也没有派给谁，更没有任何 Worker 在为你跑东西。'
    '不许说"接单了"、"我让Worker去跑"、"已经丢给谁"或任何表示已经派下去、已经在跑的话。'
    '你这一轮只能问清楚要做什么，然后等老板回答。'
)
PROGRESS_GUARD = (
    '这一轮你手上没有正在进行的督办，拿不到任何进度数据，也没有任何人替你查过。'
    '不许编造进度、阶段或数字；只能问清楚是哪一单，或者说明你现在查不到。'
)

# Trace labels. Kept as constants so the evaluation set can key off them.
SUSPECT_DELEGATION = 'delegation'
SUSPECT_PROGRESS = 'progress'


def classify(text: str) -> str:
    """Which suspicion the utterance raises: 'delegation', 'progress', or ''."""
    if not isinstance(text, str) or not text.strip():
        return ''
    try:
        if _PROGRESS_SUSPECT.search(text):
            return SUSPECT_PROGRESS
        if _DELEGATION_SUSPECT.search(text):
            return SUSPECT_DELEGATION
    except Exception:  # noqa: BLE001 - never break the voice fast loop
        return ''
    return ''


def guard_for(text: str) -> str:
    """Constraint text for this turn, or '' when nothing is suspected.

    Ordering matters: a progress question ("那事儿办得咋样了") also matches the
    delegation patterns, and telling her "do not claim acceptance" would be the
    wrong correction, so progress is checked first.
    """
    kind = classify(text)
    if kind == SUSPECT_PROGRESS:
        return PROGRESS_GUARD
    if kind == SUSPECT_DELEGATION:
        return DELEGATION_GUARD
    return ''
