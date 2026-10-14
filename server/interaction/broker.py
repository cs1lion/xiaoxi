"""Scene-scoped behavior channel. No raw evidence is injected or cached.

Preference reads happen each turn, so deletion/revision takes effect immediately.
This broker never grants tool permissions or transports personal facts to tools.
"""
from __future__ import annotations

import re
from typing import Any
from ..dialogue.composer import ResponseComposer


_LANGUAGE = r'(?<![A-Za-z0-9_])(?:Python|API|JavaScript|PowerShell|Bash|SQL)(?![A-Za-z0-9_])'
_SCRIPT = r'(?:脚本|(?<![A-Za-z0-9_])script(?![A-Za-z0-9_]))'
_FILE_OBJECT = r'(?:文件|目录|文件夹|日志|表格|数据|图片|照片|视频|(?<![A-Za-z0-9_])(?:CSV|JSON|Excel)(?![A-Za-z0-9_]))'
_TECHNICAL = re.compile(
    r'代码|报错|调试|编程|编译|命令行|异常堆栈|'
    + _LANGUAGE, re.I)
_CREATIVE_SCRIPT = re.compile(r'剧本|台词|分镜|演讲稿|(?:演讲|电影|视频|广告|直播|访谈|短片|故事|教学)(?:的)?脚本')
_TECHNICAL_TARGET = re.compile(r'(?:检查|修复|调试|解释|分析|重构|运行|执行).{0,16}(?:代码|程序|' + _LANGUAGE + r')', re.I)
_REPORTED_CLAUSE = re.compile(
    r'^\s*(?:(?:我(?:只是)?(?:在)?|只是(?:在)?|正在)?(?:转述|引用)|'
    r'(?:原话|原文|台词)(?:是|[:：])|(?:这是|这句话).{0,8}(?:别人|朋友|同事|他|她)说|'
    r'(?:我(?:的)?)?(?:他|她|他们|别人|同事|朋友|老师|有人).{0,4}(?:说|让我|要求|问))')
_CURRENT_AFTER_REPORT = re.compile(r'我(?:想|要|需要|希望)|请你|(?:但|不过|而)(?:现在)?(?:请|帮我)')
_TECHNICAL_OBJECT = r'(?:' + _LANGUAGE + r'(?:的)?(?:代码|脚本)?|代码|编程|调试|脚本)'
_DECLINED_TECHNICAL = re.compile(
    r'(?:不想|不打算|不准备|不需要|不用|不要|别|不是让你|不是要你)'
    r'(?:再|继续|现在|去|你|帮我)?'
    r'(?:(?:写|编写|编|调试|分析|解释|提供|给我|看|学|研究)[^，。；!?但却而]{0,18}?'
    + _TECHNICAL_OBJECT + r'|' + _TECHNICAL_OBJECT + r')', re.I)
_SCRIPT_REQUEST = re.compile(
    r'(?:写|做|生成|实现|修改|改|检查|排查|调试|提供|给).{0,28}' + _SCRIPT + r'|'
    + _SCRIPT + r'.{0,10}(?:怎么写|如何写|怎么改|如何改|改一下|修改)', re.I)
_FILE_OPERATION = re.compile(
    r'(?:重命名|改名|整理|合并|去重|读取|删除|备份|转换|移动|复制|筛选|处理).{0,12}'
    + _FILE_OBJECT + r'|' + _FILE_OBJECT + r'.{0,12}'
    r'(?:重命名|改名|整理|合并|去重|读取|删除|备份|转换|移动|复制|筛选|处理)', re.I)


def _scene_clauses(text: str) -> list[str]:
    """Small current-request vocabulary, not general speech understanding.

    Remove quotations and explicitly declined technical actions before looking
    for activity. A reported clause cannot stand in for the user's request; a
    separate current request after it still participates in scene selection.
    """
    clean = ResponseComposer._unquoted(text)
    clean = re.sub(r"'[^']*'|『[^』]*』", '', clean)
    clauses = []
    reported = False
    for clause in re.split(r'[，。！？；,;!?\n]+', clean):
        if _REPORTED_CLAUSE.search(clause):
            reported = True
            continue
        if reported and not _CURRENT_AFTER_REPORT.search(clause):
            continue
        reported = False
        clauses.append(_DECLINED_TECHNICAL.sub('', clause))
    return clauses


def _is_technical(clause: str) -> bool:
    # A teaching/video script can mention code without asking for code. An
    # explicitly targeted check of code inside such material remains technical.
    if _CREATIVE_SCRIPT.search(clause) and not _TECHNICAL_TARGET.search(clause):
        return False
    return bool(_TECHNICAL.search(clause) or
                (_SCRIPT_REQUEST.search(clause) and _FILE_OPERATION.search(clause)))


class ContextBroker:
    def __init__(self, preferences=None, composer=None):
        self.preferences = preferences
        self.composer = composer or ResponseComposer()

    @staticmethod
    def scene(text: str, session_id: str = '', *, activity: str | None = None,
              topic: str | None = None) -> dict[str, Any]:
        clauses = _scene_clauses(text)
        clean = '，'.join(clauses)
        detected = 'chat'
        if any(_is_technical(clause) for clause in clauses):
            detected = 'technical'
        elif re.search(r'写作|周报|文章|起草|报告|文案', clean) or _CREATIVE_SCRIPT.search(clean):
            detected = 'writing'
        elif re.search(r'工作|会议|项目|任务|截止', clean):
            detected = 'work'
        if activity is not None and activity not in ('chat', 'work', 'writing', 'technical'):
            raise ValueError('invalid_activity')
        return {'activity': activity or detected, 'session_id': session_id,
                'topic': topic, 'source': 'explicit_context' if activity else 'conservative_rule'}

    @staticmethod
    def matches(scope: dict, scene: dict) -> bool:
        return all(scene.get(key) == value for key, value in scope.items())

    def build(self, text: str, session_id: str = '', *, enabled: bool = True,
              activity: str | None = None, topic: str | None = None,
              include_thread: bool = True) -> dict[str, Any]:
        scene = self.scene(text, session_id, activity=activity, topic=topic)
        selected = {}
        excluded = []
        grouped = {}
        if enabled and self.preferences is not None:
            for claim in self.preferences.list_claims(include_inactive=True):
                identifier = claim['id']
                if claim['status'] != 'active':
                    excluded.append({'id': identifier, 'reason': 'inactive'})
                    continue
                scope = claim.get('scope') or {}
                if not self.matches(scope, scene):
                    excluded.append({'id': identifier, 'reason': 'scope_mismatch'})
                    continue
                grouped.setdefault(claim['dimension'], []).append(claim)
        for dimension, claims in grouped.items():
            specificity = max(len(c.get('scope') or {}) for c in claims)
            candidates = [c for c in claims if len(c.get('scope') or {}) == specificity]
            if len({c['value'] for c in candidates}) != 1:
                excluded.extend({'id': c['id'], 'reason': 'ambiguous_scope'} for c in candidates)
                continue
            selected[dimension] = {'value': candidates[0]['value'],
                                   'claim_ids': [c['id'] for c in candidates]}
        mapping = {'humor': 'humor', 'detail': 'length', 'support_style': 'support',
                   'question_style': 'questions'}
        defaults = {mapping[k]: v['value'] for k, v in selected.items() if k in mapping}
        # A present action request must not be blocked by a general listen preference.
        current = self.composer.preferences(text)
        if current.get('support') != 'listen' and re.search(r'帮我|请.*(?:写|整理|创建|设置)|怎么解决', self.composer._unquoted(text)):
            defaults['support'] = 'help'
        policy = self.composer.compose(text, session_id=session_id,
                                       memory_enabled=False, preference_defaults=defaults)
        from .drafting import is_drafting, DRAFT_RULES
        if is_drafting(text):
            policy['draft_grounding'] = DRAFT_RULES
        policy['preference_claim_ids'] = [identifier for v in selected.values() for identifier in v['claim_ids']]
        participation = getattr(self, 'participation', None)
        if participation is not None and enabled and include_thread:
            thread = participation.context(query=text)
            if thread:
                policy['companion_thread'] = thread
        policy['participation_act'] = {
            'listen': 'listen', 'boundary': 'acknowledge', 'correction': 'clarify',
            'celebrate': 'acknowledge', 'help': 'suggest',
        }.get(policy['intent'], 'respond')
        # Trace contains identifiers/reasons only; raw evidence stays in its own domain.
        return {'schema_version': 1, 'scene': scene, 'response_policy': policy,
                'trace': {'selected': selected, 'excluded': excluded},
                'tool_authorization': False}

    def render(self, bundle: dict[str, Any]) -> str:
        return self.composer.render(bundle['response_policy'])
