"""Synthetic boundary fixtures for the four named procedural-memory boundaries.

Shape is enforced twice: ``eval_procedural_memory_boundaries.load_sources()``
fixes the ids/roles/budget, and ``procedural_memory_eval_domain._check_recipe()``
raises ``source_recipe_materialization_failed`` unless the recipe really
produces the named boundary (truncated/ambiguous/concurrent/missing-capability).
So a fixture that does not trigger the promised behaviour cannot fake a pass.
"""

from __future__ import annotations

from pathlib import Path

import pm_eval_fixture

ROOT = Path(__file__).resolve().parents[1]
BOUNDARY_PATH = ROOT / 'eval/research/procedural_memory_boundary_cases_v1.json'

BOUNDARY_IDS = {
    'truncated_ambiguous_rejection': 'boundary-truncated',
    'concurrent_update_unique_match': 'boundary-concurrent-unique',
    'concurrent_update_ambiguous_match': 'boundary-concurrent-ambiguous',
    'missing_confirm_capability': 'boundary-missing-confirm',
}

_COMMON_CAPABILITIES = [
    'NoteStore.get_version', 'NoteStore.get', 'NoteConversation._apply',
    'NoteProposalService.propose', 'NoteProposalService.confirm',
]

# The visible projection keeps roughly 4000 characters; two repeated occurrences
# of the old_text inside that window make the preview ambiguous, and the long
# body makes the first read report body_truncated=True.
_TRUNCATED_BODY = [
    {'literal': '【合成边界】截断与歧义材料。\n'},
    {'literal': '待核对条款。待核对条款。\n'},
    {'repeat': '补充材料条目：保持原始记录。', 'count': 700},
]

_UNIQUE_BODY = [
    {'literal': '【合成边界】并发唯一匹配材料。\n'},
    {'literal': '发布候选说明：等待核对上线时间。\n'},
    {'literal': '补充说明：保持当前发布候选。'},
]

_AMBIGUOUS_BODY = [
    {'literal': '【合成边界】并发歧义匹配材料。\n'},
    {'literal': '发布候选说明：等待核对上线时间。\n'},
    {'literal': '补充说明：保持当前发布候选。'},
]

_MISSING_BODY = [
    {'literal': '【合成边界】缺少确认能力材料。\n'},
    {'literal': '会议摘记：还未整理会议摘记。\n'},
    {'literal': '补充说明：等待下一次整理。'},
]


def _recipe(recipe_id, role, goal, title, body_recipe, old_text, new_text, **extra):
    recipe = {
        'id': recipe_id,
        'host_only_role': role,
        'user_goal': goal,
        'note_fixture': {'title': title, 'body_recipe': body_recipe},
        'environment_controller_stub': {
            'action': 'edit',
            'replacements': [{'old_text': old_text, 'new_text': new_text}],
        },
        'host_confirmation_policy': 'fresh event after actual preview',
        'controlled_assistant_message': '合成边界夹具：不声称真实模型输出。',
    }
    recipe.update(extra)
    return recipe


def build_cases():
    return {
        'schema_version': 1,
        'provenance': {'note': 'synthetic boundary fixture built by pm_boundary_fixture.py'},
        'scope': 'synthetic contract test for four named boundaries',
        'budget': {
            'generation_requests': 4,
            'max_http_requests_per_generation': 1,
            'max_attempts_per_request': 1,
            'max_requested_output_tokens_per_http_request': 6000,
            'requested_output_token_upper_bound': 24000,
            'automatic_parse_repairs': False,
            'hidden_retries': 0,
        },
        'isolation': {'network': False, 'credentials': False, 'production_store': False},
        'source_bundles': [
            {'id': 'boundary-truncated', 'recipes': [_recipe(
                'boundary-truncated', 'truncated_ambiguous_rejection',
                '把截断材料里待核对的那条条款标记完成',
                '合成边界：截断材料', _TRUNCATED_BODY,
                '待核对条款。', '已核对条款。',
                host_attempt_confirm_after_rejection=True)]},
            {'id': 'boundary-concurrent-unique', 'recipes': [_recipe(
                'boundary-concurrent-unique', 'concurrent_update_unique_match',
                '核对发布候选说明里等待确认的上线时间',
                '合成边界：并发唯一', _UNIQUE_BODY,
                '等待核对上线时间', '等待核对上线时间（已核对）',
                concurrent_append_literal='\n并发追加：保持原样。')]},
            {'id': 'boundary-concurrent-ambiguous', 'recipes': [_recipe(
                'boundary-concurrent-ambiguous', 'concurrent_update_ambiguous_match',
                '核对发布候选说明里等待确认的上线时间',
                '合成边界：并发歧义', _AMBIGUOUS_BODY,
                '等待核对上线时间', '等待核对上线时间（已核对）',
                concurrent_append_literal='\n并发追加：等待核对上线时间再次出现。')]},
            {'id': 'boundary-missing-confirm', 'recipes': [_recipe(
                'boundary-missing-confirm', 'missing_confirm_capability',
                '把会议摘记里还未整理的那条整理出来',
                '合成边界：缺少确认能力', _MISSING_BODY,
                '还未整理会议摘记', '已整理会议摘记',
                host_capabilities=[item for item in _COMMON_CAPABILITIES
                                   if item != 'NoteProposalService.confirm'])]},
        ],
    }


def valid(value):
    if not isinstance(value, dict):
        return False
    bundles = value.get('source_bundles')
    if (set(value) != {'schema_version', 'provenance', 'scope', 'budget', 'isolation', 'source_bundles'}
            or value.get('schema_version') != 1 or not isinstance(bundles, list) or len(bundles) != 4
            or len({bundle.get('id') for bundle in bundles}) != 4
            or {bundle.get('id') for bundle in bundles} != set(BOUNDARY_IDS.values())
            or any(len(bundle.get('recipes') or ()) != 1 for bundle in bundles)):
        return False
    for bundle in bundles:
        role = (bundle['recipes'][0] or {}).get('host_only_role')
        if BOUNDARY_IDS.get(role) != bundle.get('id'):
            return False
    budget = value.get('budget') or {}
    return budget.get('generation_requests') == 4


def ensure():
    return pm_eval_fixture.ensure_file(BOUNDARY_PATH, build_cases(), 'boundary-cases', valid)
