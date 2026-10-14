"""Offline mechanism probe: manual experience candidates, temporary notes only.

This is not an LLM extraction, natural-language retrieval, or efficacy evaluation.
Only independent domain modules are loaded; configuration and app bootstrap are
never imported. Advice is data and historical approvals are never replayed.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import types


def domain_modules():
    root = Path(__file__).resolve().parents[1] / 'server' / 'assistant'
    package = types.ModuleType('_procedural_probe_domain')
    package.__path__ = [str(root)]
    sys.modules[package.__name__] = package
    for name in ('context', 'notes', 'note_proposals', 'note_conversation'):
        spec = importlib.util.spec_from_file_location(package.__name__ + '.' + name, root / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
    return tuple(sys.modules[package.__name__ + '.' + name] for name in
                 ('notes', 'note_proposals', 'note_conversation'))


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def reference(note):
    return {'type': 'note', 'id': note['id'], 'version': note['version']}


def stamped(note):
    return {'ref': reference(note), 'body_sha256': hashlib.sha256(note['body'].encode()).hexdigest()}


FIELDS = {'schema_version', 'record_type', 'candidate_origin', 'task_family',
          'requires', 'required_capabilities', 'advice', 'evidence',
          'validation_scope', 'grants_authorization'}
CAPABILITIES = {'NoteConversation._apply', 'NoteProposalService.propose', 'NoteProposalService.confirm'}
SCOPE = 'existing_domain_boundaries_and_this_local_patch_observation_only'


def validate(record, catalog, store, forbidden):
    if (not isinstance(record, dict) or set(record) != FIELDS or type(record['schema_version']) is not int or record['schema_version'] != 1
            or record['record_type'] not in ('procedure', 'failure_lesson')
            or record['candidate_origin'] != 'manual_script_not_llm'
            or record['task_family'] != 'saved_note_local_patch'
            or record['validation_scope'] != SCOPE or record['grants_authorization'] is not False):
        raise ValueError('invalid_schema_or_claimed_authority')
    requirements, capabilities = record['requires'], record['required_capabilities']
    if (not isinstance(requirements, dict) or not requirements
            or set(requirements) - {'saved_current_version', 'unique_visible_match', 'explicit_advice_request'}
            or any(type(value) is not bool for value in requirements.values())
            or not isinstance(capabilities, list) or not capabilities or set(capabilities) - CAPABILITIES):
        raise ValueError('invalid_applicability')
    if (not isinstance(record['advice'], list) or not 1 <= len(record['advice']) <= 8
            or any(not isinstance(step, str) or not step.strip() or len(step) > 300 for step in record['advice'])):
        raise ValueError('invalid_advice')
    if any(value in encoded(record) for value in forbidden):
        raise ValueError('historical_object_or_approval_not_generalized')
    if not isinstance(record['evidence'], list) or not 1 <= len(record['evidence']) <= 4:
        raise ValueError('invalid_evidence')
    claims = set()
    for link in record['evidence']:
        if (not isinstance(link, dict) or set(link) != {'episode_id', 'observation_index', 'claim'}
                or type(link['observation_index']) is not int or link['observation_index'] < 0):
            raise ValueError('invalid_evidence_link')
        episode = catalog.get(link['episode_id'])
        if episode is None or episode['origin'] != 'host_observation':
            raise ValueError('missing_host_episode')
        try:
            observation = episode['observations'][link['observation_index']]
        except IndexError:
            raise ValueError('missing_host_observation') from None
        if link['claim'] != observation['kind']:
            raise ValueError('claim_not_supported_by_host_observation')
        sources = observation['saved_refs']
        if not isinstance(sources, list) or len(sources) < (2 if link['claim'] == 'local_patch_saved' else 1):
            raise ValueError('missing_saved_version_evidence')
        if link['claim'] == 'local_patch_saved' and (sources[0]['ref']['id'] != sources[1]['ref']['id']
                or sources[1]['ref']['version'] != sources[0]['ref']['version'] + 1):
            raise ValueError('not_one_observed_version_transition')
        for evidence in sources:
            ref = evidence['ref']
            try:
                actual = store.get_version(ref['id'], ref['version'])
            except KeyError:
                raise ValueError('source_version_unavailable') from None
            if stamped(actual) != evidence:
                raise ValueError('source_version_mismatch')
        claims.add(link['claim'])
    needed = {'local_patch_saved'} if record['record_type'] == 'procedure' else {
        'ambiguous_match_rejected', 'base_conflict_without_retry'}
    if not claims.intersection(needed):
        raise ValueError('no_observed_outcome_for_record_type')


def retrieve(store, task, catalog, forbidden):
    """Explicit family/feature matching, not semantic or similarity retrieval."""
    try:
        ref = task['target_ref']
        if (not isinstance(ref, dict) or set(ref) != {'type', 'id', 'version'} or ref['type'] != 'note'
                or not isinstance(ref['id'], str) or type(ref['version']) is not int):
            raise ValueError('invalid_target_reference')
        if reference(store.get(ref['id'])) != ref or reference(store.get_version(ref['id'], ref['version'])) != ref:
            raise ValueError('target_not_current')
    except (ValueError, TypeError, KeyError):
        return {'matches': [], 'rejected': [{'reason': 'target_not_current_or_missing'}]}
    matches, rejected = [], []
    for info in store.search('离线经验：'):
        note = store.get(info['id'])
        try:
            record = json.loads(note['body'])
            validate(record, catalog, store, forbidden)
            if record['task_family'] != task['family']:
                raise ValueError('family_mismatch')
            if any(task['features'].get(key) is not value for key, value in record['requires'].items()):
                raise ValueError('missing_or_mismatched_precondition')
            if not set(record['required_capabilities']).issubset(task['capabilities']):
                raise ValueError('missing_capability')
            matches.append({'procedure_ref': reference(note), 'target_ref': dict(task['target_ref']),
                            'advice': record['advice'], 'grants_authorization': False,
                            'record_type': record['record_type']})
        except (ValueError, TypeError, KeyError) as error:
            rejected.append({'card_id': note['id'], 'reason': str(error)})
    return {'matches': matches, 'rejected': rejected}


def probe():
    notes_module, proposals_module, conversation_module = domain_modules()
    checks, catalog, forbidden = [], {}, set()

    def check(condition, name):
        if not condition:
            raise AssertionError(name)
        checks.append({'name': name, 'passed': True})

    with tempfile.TemporaryDirectory(prefix='aqi-procedural-probe-') as directory:
        store = notes_module.NoteStore(Path(directory) / 'notes.sqlite')
        proposals = proposals_module.NoteProposalService(store)
        original = store.create('合成局部修改源稿', '目前还在查登录问题。\n' + '保留内容。' * 900 + '\nKEEP_TAIL', 'offline synthetic')
        forbidden.add(original['id'])
        chosen = store.select(original['id'], version=1, expected_revision=store.selection()['revision'])
        action, title, body = conversation_module.NoteConversation._apply({'action': 'edit',
            'replacements': [{'old_text': '还在查登录问题', 'new_text': '持续排查登录问题'}]},
            original, original['body'][:4000])
        check(action == 'edit' and body == original['body'].replace('还在查登录问题', '持续排查登录问题', 1)
              and body.endswith('KEEP_TAIL') and 'KEEP_TAIL' not in original['body'][:4000],
              'local_patch_preserves_unseen_tail')
        candidate = proposals.propose(original['id'], 1, title, body, '把原文的还在查登录问题改成持续排查登录问题。')
        forbidden.add(candidate['id'])
        preview_unchanged = store.get(original['id']) == original
        check(preview_unchanged, 'proposal_does_not_save')
        saved = proposals.confirm(candidate['id'], 1)
        check(saved['version'] == 2 and saved['body'] == body
              and store.get_version(original['id'], 1) == original
              and store.selection()['selected_version'] == chosen['selected_version'],
              'confirmation_saves_exact_new_version_without_promoting_selection')
        observations = [
            {'kind': 'preview_without_save', 'saved_refs': [stamped(original)], 'write_observed': False},
            {'kind': 'local_patch_saved', 'saved_refs': [stamped(original), stamped(saved)],
             'verification': 'exact_body_readback_and_immutable_original'},
        ]
        try:
            conversation_module.NoteConversation._apply({'action': 'edit', 'replacements': [
                {'old_text': '保留内容。', 'new_text': '改动。'}]}, saved, saved['body'][:4000])
        except ValueError:
            observations.append({'kind': 'ambiguous_match_rejected', 'saved_refs': [stamped(saved)]})
        else:
            raise AssertionError('ambiguous patch accepted')
        conflict = proposals.propose(saved['id'], 2, title, body + '\n人工候选', '增加一行。')
        forbidden.add(conflict['id'])
        current = store.update(saved['id'], title, body + '\n另一窗口的明确修改', 2)
        update_attempts, real_update = [], store.update

        def counted_update(*args, **kwargs):
            update_attempts.append(1)
            return real_update(*args, **kwargs)

        store.update = counted_update
        try:
            proposals.confirm(conflict['id'], 1)
        except proposals_module.NoteProposalConflict:
            pass
        else:
            raise AssertionError('stale proposal confirmed')
        check(not update_attempts and store.get(current['id']) == current, 'base_conflict_has_no_write_attempt_or_retry')
        store.update = real_update
        observations.append({'kind': 'base_conflict_without_retry', 'saved_refs': [stamped(saved), stamped(current)],
                             'write_attempts': len(update_attempts), 'automatic_retries': 0})
        catalog['episode-local-patch'] = {'origin': 'host_observation', 'observations': observations}
        record = {'schema_version': 1, 'record_type': 'procedure', 'candidate_origin': 'manual_script_not_llm',
            'task_family': 'saved_note_local_patch', 'requires': {'saved_current_version': True,
                'unique_visible_match': True, 'explicit_advice_request': True},
            'required_capabilities': sorted(CAPABILITIES), 'advice': ['先核对当前保存版本与唯一原文，再提出局部替换预览。',
                '人工核对后确认保存；未展示尾部保持原样，版本冲突时重新核对，不自动覆盖。'],
            'evidence': [{'episode_id': 'episode-local-patch', 'observation_index': 1, 'claim': 'local_patch_saved'}],
            'validation_scope': SCOPE, 'grants_authorization': False}
        validate(record, catalog, store, forbidden)
        invalid_links = [dict(episode_id='invented', observation_index=1, claim='local_patch_saved'),
                         dict(episode_id='episode-local-patch', observation_index=0, claim='local_patch_saved')]
        for link in invalid_links:
            invalid = deepcopy(record)
            invalid['evidence'] = [link]
            try:
                validate(invalid, catalog, store, forbidden)
            except ValueError:
                pass
            else:
                raise AssertionError('unsupported evidence accepted')
        for refs in ([], observations[1]['saved_refs'][:1]):
            unsupported = deepcopy(catalog)
            unsupported['episode-local-patch']['observations'][1]['saved_refs'] = refs
            try:
                validate(record, unsupported, store, forbidden)
            except ValueError:
                pass
            else:
                raise AssertionError('saved label without version evidence accepted')
        check(True, 'invented_reference_unexecuted_success_and_missing_version_evidence_rejected')
        card = store.create('离线经验：局部修改做法', encoded(record), 'procedure_candidate')
        revision = deepcopy(record)
        revision['advice'].append('一次观察不证明该做法普遍有效；新任务需要重新检查前提。')
        validate(revision, catalog, store, forbidden)
        reviewed = proposals.propose(card['id'], 1, card['title'], encoded(revision), '脚本明确补充适用限制。')
        forbidden.add(reviewed['id'])
        updated_card = proposals.confirm(reviewed['id'], 1)
        check(updated_card['version'] == 2 and json.loads(store.get_version(card['id'], 1)['body']) == record,
              'manual_procedure_candidate_uses_existing_note_revision_and_review')
        lesson = deepcopy(record)
        lesson.update(record_type='failure_lesson', required_capabilities=['NoteConversation._apply'],
            advice=['原文重复或匹配不唯一时先澄清，不能猜位置；这次拒绝修改是边界观察，并非完成修改。'],
            evidence=[{'episode_id': 'episode-local-patch', 'observation_index': 2, 'claim': 'ambiguous_match_rejected'}])
        lesson['requires']['unique_visible_match'] = False
        validate(lesson, catalog, store, forbidden)
        lesson_card = store.create('离线经验：失败教训', encoded(lesson), 'procedure_candidate')
        forged = deepcopy(record)
        forged['evidence'][0]['episode_id'] = 'invented'
        store.create('离线经验：自报来源负例', encoded(forged), 'procedure_candidate')
        target = store.create('新的独立稿件', '仍在整理输入目录。\n新对象的结尾。', 'offline new task')
        store.select(target['id'], version=1, expected_revision=store.selection()['revision'])
        task = {'family': 'saved_note_local_patch', 'target_ref': reference(target),
                'features': dict(record['requires']), 'capabilities': CAPABILITIES}
        found = retrieve(store, task, catalog, forbidden)
        check(len(found['matches']) == 1 and found['matches'][0]['procedure_ref'] == reference(updated_card)
              and original['id'] not in encoded(found['matches']) and store.get(target['id']) == target
              and found['matches'][0]['grants_authorization'] is False
              and any(item['reason'] == 'missing_host_episode' for item in found['rejected']),
              'explicit_family_match_returns_advice_for_new_object_without_authority_or_old_ids')
        for field, value in [('family', 'unrelated'), ('capabilities', set()), ('features', {})]:
            mismatch = {**task, field: value}
            check_result = retrieve(store, mismatch, catalog, forbidden)
            if check_result['matches']:
                raise AssertionError('inapplicable procedure returned')
        negative_task = {**task, 'features': {**task['features'], 'unique_visible_match': False}}
        negative = retrieve(store, negative_task, catalog, forbidden)
        check(len(negative['matches']) == 1 and negative['matches'][0]['procedure_ref'] == reference(lesson_card),
              'missing_preconditions_or_capabilities_block_reuse_and_failure_lesson_remains_readable')
        # Independent hand-authored model-result stub; retrieved advice is never parsed or executed.
        stub = {'action': 'edit', 'replacements': [{'old_text': '仍在整理输入目录', 'new_text': '开始整理输入目录'}]}
        _, new_title, new_body = conversation_module.NoteConversation._apply(stub, target, target['body'][:4000])
        new_proposal = proposals.propose(target['id'], 1, new_title, new_body, '把仍在整理输入目录改成开始整理输入目录。')
        check(store.get(target['id']) == target, 'new_object_proposal_does_not_save')
        try:
            proposals.confirm(candidate['id'], 1)
        except KeyError:
            pass
        else:
            raise AssertionError('old confirmation token replayed')
        new_saved = proposals.confirm(new_proposal['id'], 1)
        check(new_saved['version'] == 2 and new_saved['body'] == target['body'].replace('仍在整理输入目录', '开始整理输入目录', 1)
              and store.get(new_saved['id']) == new_saved and store.get(current['id']) == current
              and store.get_version(target['id'], 1) == target and store.selection()['selected_version'] == 1,
              'independent_stub_reuses_domain_steps_with_fresh_confirmation_and_old_object_unchanged')
        stale = retrieve(store, task, catalog, forbidden)
        check(not stale['matches'] and stale['rejected'][0]['reason'] == 'target_not_current_or_missing',
              'changed_target_version_blocks_advice_despite_declared_current_feature')
        task['target_ref'] = reference(new_saved)
        catalog['episode-new-object'] = {'origin': 'host_observation', 'observations': [
            {'kind': 'local_patch_saved', 'saved_refs': [stamped(target), stamped(new_saved)],
             'verification': 'independent_hand_authored_stub_new_confirmation_exact_readback'}]}
        store.delete(current['id'], current['version'])
        check(not retrieve(store, task, catalog, forbidden)['matches'], 'deleted_exact_source_disables_experience_reuse')
        store.delete(updated_card['id'], updated_card['version'])
        check(not any(item['id'] == card['id'] for item in store.search('离线经验：')),
              'deleted_experience_card_is_not_retrievable')
        return {'status': 'passed', 'checks': checks, 'candidate_origin': 'manual_script_not_llm',
            'host_episodes': catalog, 'stored_candidate_schema': record,
            'scope': {'storage': 'TemporaryDirectory SQLite, cleaned on exit',
                      'matching': 'explicit task-family and declared feature/capability matching',
                      'validation': SCOPE, 'source_label_is_authentication': False,
                      'llm_extraction_tested': False, 'semantic_generalization_tested': False,
                      'natural_language_retrieval_quality_tested': False, 'automatic_execution': False,
                      'independent_stub_domain_reuse_tested': True, 'advice_interpreted_as_commands': False}}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, help='Write the JSON report; default is stdout.')
    args = parser.parse_args()
    try:
        report, code = probe(), 0
    except Exception as error:
        report, code = {'status': 'failed', 'error_type': type(error).__name__, 'error': str(error)}, 1
    text = json.dumps(report, ensure_ascii=False, indent=2) + '\n'
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding='utf-8')
    else:
        sys.stdout.write(text)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
