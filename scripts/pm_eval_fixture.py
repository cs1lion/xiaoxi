"""Synthetic, offline domain fixtures shared by the procedural-memory gates.

The original ``eval/research/*.json`` fixtures were deleted with the ``/eval``
cleanup and never entered git.  This module rebuilds the *source* half of those
fixtures from the shape that ``procedural_memory_eval_domain.SourceCorpus`` and
``_check_recipe()`` mechanically enforce, plus a clearly labelled synthetic
replay input for the diagnostics pipeline.  No model key, network, production
store or real user data is involved.

Consumers call ``ensure_cases()`` / ``ensure_diagnostic_inputs()`` before they
compute their import-time availability constant, so a missing fixture no longer
turns into a permanent SKIP.  Files are written under the gitignored
``eval/research/`` path because subprocess-based snapshot tests can only read a
fixed on-disk path.

Concurrency (``ci_test_shard.py --workers 4``): writers take a file lock and
register a per-process lease.  The process that created a file removes it only
after the last lease is gone; readers never overwrite a pre-existing fixture.
"""

from __future__ import annotations

import atexit
import hashlib
import json
import os
from pathlib import Path
from contextlib import contextmanager

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / 'eval/research/procedural_memory_eval_cases.json'
INPUT_PATH = ROOT / 'eval/research/procedural_memory_diagnostic_inputs_v1.json'
LOCK_NAME = '.pm-eval-fixture.lock'

INPUT_IDS = ('simple-json-control', 'source-local-v2-replay', 'source-progress-v2-replay')


def fingerprint(value):
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(encoded.encode('utf-8')).hexdigest()


# --------------------------------------------------------------------------
# Shared cases fixture: 3 bundles x 2 recipes.
# --------------------------------------------------------------------------

_LOCAL_BODY = [
    {'literal': '【合成材料】本周纪要整理记录。\n'},
    {'literal': '待整理纪要：会议时间。\n'},
    {'literal': '待核对条款。待核对条款。\n'},
    # Long enough that the public note body is repeated many times across the
    # two episodes; the projection's text table then deduplicates it and the
    # projected bundle is smaller than the raw one.
    {'repeat': '补充条目：保持原始记录。', 'count': 90},
]

_VERSION_BODY = [
    {'literal': '【合成材料】版本核对。\n'},
    {'literal': '发布候选说明：等待核对上线时间。\n'},
    {'literal': '补充说明：保持当前发布候选。'},
]

_PROGRESS_BODY = [
    {'literal': '【合成材料】进度同步。\n'},
    {'literal': '进度记录：当前状态待更新。\n'},
    {'literal': '补充说明：等待下一次同步。'},
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
    }
    recipe.update(extra)
    return recipe


def build_sources():
    """Return the synthetic source specification.

    ``source-local-b`` deliberately leaves a rejected preview without a confirm
    attempt (3 observations), while ``source-version-b`` materialises the
    concurrent fault (5 observations).  The remaining four recipes contribute
    4 each, so ``SourceCorpus.prepare()`` reports exactly 24 observations.
    """
    local = [
        _recipe('source-local-a', 'default_local_edit',
                '把本周纪要里待整理的那条时间补全',
                '合成材料：本周纪要整理', _LOCAL_BODY,
                '待整理纪要：会议时间。', '已整理纪要：会议时间。'),
        _recipe('source-local-b', 'ambiguous_location_failure',
                '把本周纪要里待核对的那条条款标记完成',
                '合成材料：本周纪要整理', _LOCAL_BODY,
                '待核对条款。', '已核对条款。'),
    ]
    version = [
        _recipe('source-version-a', 'default_local_edit',
                '核对发布候选说明里等待确认的上线时间',
                '合成材料：版本核对', _VERSION_BODY,
                '等待核对上线时间', '等待核对上线时间已确认'),
        _recipe('source-version-b', 'concurrent_update_unique_match',
                '核对发布候选说明里等待确认的上线时间',
                '合成材料：版本核对', _VERSION_BODY,
                '等待核对上线时间', '等待核对上线时间（已核对）',
                concurrent_append_literal='\n并发追加：保持原样。'),
    ]
    progress = [
        _recipe('source-progress-a', 'default_local_edit',
                '把进度同步里当前状态那条更新为已更新',
                '合成材料：进度同步', _PROGRESS_BODY,
                '当前状态待更新', '当前状态已更新'),
        _recipe('source-progress-b', 'default_local_edit',
                '把进度同步里当前状态那条标记为解决',
                '合成材料：进度同步', _PROGRESS_BODY,
                '当前状态待更新', '已解决：当前状态已更新'),
    ]
    return {'source_bundles': [
        {'id': 'source-local', 'recipes': local},
        {'id': 'source-version', 'recipes': version},
        {'id': 'source-progress', 'recipes': progress},
    ]}


def cases_valid(value, *, require_version=False):
    bundles = value.get('source_bundles') if isinstance(value, dict) else None
    if (not isinstance(bundles, list) or len(bundles) != 3
            or len({bundle.get('id') for bundle in bundles}) != 3
            or any(not isinstance(bundle.get('recipes'), list) or len(bundle['recipes']) != 2
                   for bundle in bundles)):
        return False
    if require_version:
        return any(bundle.get('id') == 'source-version'
                   and any(recipe.get('id') == 'source-version-b' for recipe in bundle['recipes'])
                   for bundle in bundles)
    return True


# --------------------------------------------------------------------------
# Diagnostics replay inputs.  Hashes are self-consistent, and the provenance
# strings say plainly that this is synthetic rather than a historical report.
# --------------------------------------------------------------------------

def build_diagnostic_inputs():
    inputs = []
    origin = fingerprint({'synthetic_replay': True, 'fixture': 'pm_eval_fixture'})
    for index, input_id in enumerate(INPUT_IDS):
        messages = [
            {'role': 'system', 'content':
             '离线合成回放：只评估结构契约，不声称来自真实历史故障报告。'},
            {'role': 'user', 'content': json.dumps(
                {'synthetic_replay': True, 'slot': index, 'input_id': input_id}, ensure_ascii=False)},
        ]
        item = {
            'id': input_id,
            'messages': messages,
            'provenance': 'synthetic_replay_not_a_historical_report',
        }
        if index:
            item['original_input_sha256'] = fingerprint(messages)
        inputs.append(item)
    return {
        'schema_version': 1,
        'origin_report_sha256': origin,
        'origin_report_path': 'eval/research/synthetic-replay-not-a-real-report.json',
        'inputs': inputs,
    }


def diagnostic_inputs_valid(value):
    if (not isinstance(value, dict)
            or set(value) != {'schema_version', 'origin_report_sha256', 'origin_report_path', 'inputs'}
            or value.get('schema_version') != 1 or not isinstance(value.get('inputs'), list)
            or [item.get('id') for item in value['inputs']] != list(INPUT_IDS)):
        return False
    for index, item in enumerate(value['inputs']):
        keys = {'id', 'messages', 'provenance'} | ({'original_input_sha256'} if index else set())
        if set(item) != keys or len(item['messages']) != 2:
            return False
        for message, role in zip(item['messages'], ('system', 'user')):
            if (set(message) != {'role', 'content'} or message['role'] != role
                    or not isinstance(message['content'], str)):
                return False
        if index and fingerprint(item['messages']) != item['original_input_sha256']:
            return False
    return True


# --------------------------------------------------------------------------
# Concurrency-safe installation.
# --------------------------------------------------------------------------

@contextmanager
def _file_lock(directory):
    directory.mkdir(parents=True, exist_ok=True)
    lock = directory / LOCK_NAME
    descriptor = os.open(lock, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b'0')
        os.lseek(descriptor, 0, os.SEEK_SET)
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_EX)
        yield
    finally:
        if os.name == 'nt':
            import msvcrt
            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _cleanup(path, lease, created, label):
    try:
        with _file_lock(path.parent):
            try:
                lease.unlink()
            except OSError:
                pass
            if created and not list(path.parent.glob(f'.pm-fixture-{label}-*.lease')):
                try:
                    path.unlink()
                except OSError:
                    pass
    except OSError:
        pass


def _ensure(path, payload, label, validator):
    with _file_lock(path.parent):
        created = not path.exists()
        if created:
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        lease = path.parent / f'.pm-fixture-{label}-{os.getpid()}.lease'
        if not lease.exists():
            lease.write_text('1', encoding='utf-8')
            atexit.register(_cleanup, path, lease, created, label)
    try:
        return validator(json.loads(path.read_text(encoding='utf-8-sig')))
    except (OSError, ValueError, TypeError, KeyError):
        return False


def ensure_file(path, payload, label, validator):
    return _ensure(path, payload, label, validator)


def ensure_cases(*, require_version=False):
    return _ensure(CASE_PATH, build_sources(), 'cases',
                   lambda value: cases_valid(value, require_version=require_version))


def ensure_diagnostic_inputs():
    return _ensure(INPUT_PATH, build_diagnostic_inputs(), 'diagnostic-inputs',
                   diagnostic_inputs_valid)
