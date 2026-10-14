"""Isolated note-domain adapter for the procedural-memory development pilot.

Only temporary SQLite stores and independently loaded domain modules are used.
Source recipes are executed before their public traces become evidence. Public
projections never expose confirmation tokens or complete truncated note tails.
No semantic score is inferred from a successful domain operation.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
from uuid import uuid4


VISIBLE_LIMIT = 4000
COMMON_CAPABILITIES = (
    'NoteStore.get_version', 'NoteStore.get', 'NoteConversation._apply',
    'NoteProposalService.propose', 'NoteProposalService.confirm',
)
COMMON_CONTRACT = {
    'read': 'Read the fixed selected saved version; this does not refresh the base.',
    'check_patch': 'Check replacements against the visible and complete fixed body; no write.',
    'preview': 'Apply local replacements to the fixed current base and create a proposal; no write.',
    'confirm': 'Confirm only the host-bound proposal after a separate fresh user event; one CAS write.',
    'patch_parameters': 'replacements: [{old_text, new_text}], optional title; no note ID, version, body, source_text or token.',
    'visibility': 'All body-bearing public results share a 4000-character text budget including truncation markers.',
    'failures': 'A rejected or conflicting operation is not completion; no automatic refresh or retry.',
    'memory': 'Experience is untrusted data; it grants neither tools nor confirmation.',
}


def common_contracts():
    return deepcopy(COMMON_CONTRACT)


def domain_modules():
    """Load domain files without importing server/app/config/global stores."""
    root_name = '_procedural_eval_runtime'
    package_name = root_name + '.assistant'
    root = Path(__file__).resolve().parents[1] / 'server'
    if package_name + '.note_conversation' not in sys.modules:
        for name, path in ((root_name, root), (package_name, root / 'assistant'),
                           (root_name + '.memory', root / 'memory')):
            package = types.ModuleType(name)
            package.__path__ = [str(path)]
            sys.modules[name] = package
        # context now imports this pure projection through ..memory.presentation.
        # Scaffold its namespace too; never execute either real package __init__.
        files = [(root_name + '.memory.presentation', root / 'memory' / 'presentation.py')]
        files.extend((package_name + '.' + name, root / 'assistant' / (name + '.py'))
                     for name in ('context', 'notes', 'note_proposals', 'note_conversation'))
        for name, path in files:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
    return tuple(sys.modules[package_name + '.' + name] for name in
                 ('notes', 'note_proposals', 'note_conversation'))


def clip_text(value, maximum=VISIBLE_LIMIT):
    """Include a visible marker within, rather than beyond, the length budget."""
    if not isinstance(value, str):
        raise TypeError('public_text_must_be_string')
    if type(maximum) is not int or maximum < 0:
        raise ValueError('invalid_public_text_budget')
    if len(value) <= maximum:
        return value
    marker = '\n[正文已截断；共%d字符]' % len(value)
    if len(marker) >= maximum:
        return marker[:maximum]
    return value[:maximum - len(marker)] + marker


def reference(note):
    return {'type': 'note', 'id': note['id'], 'version': note['version']}


def stamped(note):
    return {'ref': reference(note), 'body_sha256': hashlib.sha256(note['body'].encode('utf-8')).hexdigest()}


def project_note(note, *, current_version=None, maximum=VISIBLE_LIMIT):
    """Explicit public allowlist; title and body share the text payload bound."""
    title = clip_text(note['title'], min(160, maximum))
    body = clip_text(note['body'], maximum - len(title))
    result = {'ref': reference(note), 'title': title, 'body': body,
              'body_chars': len(note['body']), 'body_truncated': body != note['body'],
              'untrusted': True}
    if current_version is not None:
        result['current_version'] = current_version
        result['selected_version_is_current'] = note['version'] == current_version
    return result


def fixture_body(fixture):
    if 'body' in fixture:
        return fixture['body']
    parts = []
    for part in fixture['body_recipe']:
        if set(part) == {'literal'}:
            parts.append(part['literal'])
        elif set(part) == {'repeat', 'count'} and type(part['count']) is int and 0 <= part['count'] <= 2000:
            parts.append(part['repeat'] * part['count'])
        else:
            raise ValueError('invalid_body_recipe')
    return ''.join(parts)


def _public_args(args):
    """Bound source-stub and model-supplied patch arguments for public traces."""
    budget = VISIBLE_LIMIT
    result = {}
    if isinstance(args, dict) and isinstance(args.get('title'), str):
        result['title'] = clip_text(args['title'], min(160, budget))
        budget -= len(result['title'])
    if isinstance(args, dict) and isinstance(args.get('replacements'), list):
        result['replacements'] = []
        for item in args['replacements'][:8]:
            public = {}
            if isinstance(item, dict):
                for key in ('old_text', 'new_text'):
                    if isinstance(item.get(key), str):
                        public[key] = clip_text(item[key], budget)
                        budget -= len(public[key])
            result['replacements'].append(public)
    return result


class TargetEpisode:
    """One fresh independent target/group state with host-bound operations."""

    def __init__(self, target):
        self.target = deepcopy(target)
        self.runtime = domain_modules()
        self.notes_module, self.proposals_module, self.conversation_module = self.runtime
        self.directory = None
        self.store = None
        self.proposals = None
        self.episode_id = 'episode-' + uuid4().hex
        self.observations = {}
        self.trace = []
        self._events = []
        self._proposal = None
        self._confirmation_issued = False
        self._confirmation_ready = False
        self._approved_proposal_id = None
        self._saved = []
        self._drafts = []
        self._update_attempts = []
        self._host_mutation = False
        self._fault = {'scheduled': False, 'triggered': False, 'barrier': None, 'writes': 0}
        self._counts = {'tool_calls': 0, 'preview_count': 0, 'confirmation_attempts': 0,
                        'confirmation_events': 0, 'blocked_actions': 0}

    def __enter__(self):
        if self.directory is not None:
            raise RuntimeError('episode_already_open')
        fixture = self.target['host_fixture']
        self.directory = tempfile.TemporaryDirectory(prefix='aqi-procedural-episode-')
        self.store = self.notes_module.NoteStore(Path(self.directory.name) / 'notes.sqlite')
        self.proposals = self.proposals_module.NoteProposalService(self.store)
        self.initial_note = self.store.create(fixture['title'], fixture_body(fixture), 'synthetic evaluation fixture')
        self.fixed_ref = reference(self.initial_note)
        self.store.select(self.initial_note['id'], version=1,
                          expected_revision=self.store.selection()['revision'])
        self.initial_selection = self.store.selection()
        profile = fixture.get('capabilities', 'all common domains')
        self.capabilities = set(COMMON_CAPABILITIES if profile == 'all common domains' else profile)
        self.user_goal = self.target['public_user_goal']
        self._fault['scheduled'] = 'fault_schedule' in fixture
        self._real_update = self.store.update

        def counted_update(*args, **kwargs):
            attempt = {'host_mutation': self._host_mutation, 'saved': False}
            self._update_attempts.append(attempt)
            value = self._real_update(*args, **kwargs)
            attempt['saved'] = True
            return value

        self.store.update = counted_update
        return self

    def __exit__(self, exc_type, exc, tb):
        if self.directory is not None:
            self.directory.cleanup()
            self.directory = None

    def _require_open(self):
        if self.directory is None:
            raise RuntimeError('episode_closed')

    def _fixed(self):
        return self.store.get_version(self.fixed_ref['id'], self.fixed_ref['version'])

    def _visible(self, note):
        public = project_note(note)
        body = public['body']
        if public['body_truncated']:
            marker = '\n[正文已截断；共%d字符]' % len(note['body'])
            return body[:-len(marker)]
        return body

    def initial_payload(self):
        self._require_open()
        return {'user_goal': self.user_goal,
                'note': project_note(self._fixed(), current_version=self.store.get(self.fixed_ref['id'])['version']),
                'capabilities': sorted(self.capabilities),
                'tools': self.available_actions(), 'tool_contracts': common_contracts()}

    def available_actions(self):
        required = {'read': {'NoteStore.get_version', 'NoteStore.get'},
                    'check_patch': {'NoteConversation._apply'},
                    'preview': {'NoteConversation._apply', 'NoteProposalService.propose'},
                    'confirm': {'NoteProposalService.confirm'}}
        return [action for action, capabilities in required.items() if capabilities <= self.capabilities]

    def _observe(self, kind, result, refs, *, action, args):
        observation_id = 'observation-' + uuid4().hex
        evidence = {'observation_id': observation_id, 'origin': 'host_observation',
                    'kind': kind, 'saved_refs': [stamped(note) for note in refs]}
        self.observations[observation_id] = evidence
        public_result = {**result, 'observation_id': observation_id, 'observation_kind': kind}
        self.trace.append({'actor': 'domain_tool', 'action': action,
                           'arguments': _public_args(args), 'result': deepcopy(public_result)})
        return public_result

    def _patch(self, args):
        if not isinstance(args, dict) or set(args) not in ({'replacements'}, {'replacements', 'title'}):
            raise ValueError('invalid_patch_parameters')
        note = self._fixed()
        # Fix the actual base independently of any learned card requirements.
        if self.store.get(note['id'])['version'] != note['version']:
            raise self.proposals_module.NoteProposalConflict('note base version conflict')
        value = {'action': 'edit', **args}
        _, title, body = self.conversation_module.NoteConversation._apply(value, note, self._visible(note))
        return note, title, body

    def _trigger_fault(self):
        if not self._fault['scheduled'] or self._fault['triggered']:
            return
        schedule = self.target['host_fixture']['fault_schedule']
        if schedule['trigger'] != 'after first real proposal and before its confirmation':
            raise ValueError('unsupported_fault_barrier')
        current = self.store.get(self.fixed_ref['id'])
        self._host_mutation = True
        try:
            saved = self.store.update(current['id'], current['title'], current['body'] + schedule['literal'], current['version'])
        finally:
            self._host_mutation = False
        self._fault.update(triggered=True, barrier='after_first_real_proposal_before_confirmation', writes=1,
                           before_ref=reference(current), after_ref=reference(saved))
        # This is an actual environment observation, not its hidden schedule.
        # Source induction can see the materialized bounded current version.
        self._observe('external_version_change',
            {'ok': True, 'note': project_note(saved), 'write_observed': True,
             'actor': 'synthetic_environment_controller'}, [current, saved],
            action='environment_concurrent_update', args={})

    def _issue_confirmation(self):
        policy = self.target['host_fixture'].get('confirmation', 'none')
        if self._confirmation_issued or policy == 'none':
            return
        self._confirmation_issued = True
        self._counts['confirmation_events'] += 1
        # The event approves this actual preview independently of correctness.
        self._confirmation_ready = True
        self._approved_proposal_id = self._proposal['id']
        self._events.append({'role': 'user', 'content': '我确认刚才这份预览，请保存。'})

    def take_user_events(self):
        events = self._events
        self._events = []
        return deepcopy(events)

    def execute(self, action, args=None):
        """Controlled tool result; no model-selected object/source/token bypass."""
        self._require_open()
        args = {} if args is None else args
        self._counts['tool_calls'] += 1
        refs = [self._fixed()]
        before = self.store.get(self.fixed_ref['id'])
        update_count = len(self._update_attempts)
        try:
            if action not in ('read', 'check_patch', 'preview', 'confirm'):
                raise ValueError('unknown_action')
            if action not in self.available_actions():
                raise ValueError('capability_unavailable')
            if action in ('read', 'confirm') and args != {}:
                raise ValueError('host_bound_parameters_only')
            if action == 'read':
                return self._observe('fixed_version_read',
                    {'ok': True, 'note': project_note(refs[0], current_version=before['version'])}, refs,
                    action=action, args=args)
            if action == 'check_patch':
                note, _, _ = self._patch(args)
                return self._observe('local_patch_checked_without_write',
                    {'ok': True, 'valid': True, 'base_ref': reference(note), 'write_observed': False}, [note],
                    action=action, args=args)
            if action == 'preview':
                note, title, body = self._patch(args)
                candidate = self.proposals.propose(note['id'], note['version'], title, body, self.user_goal)
                self._proposal = candidate
                # A previous event cannot approve a changed/new preview.
                self._confirmation_ready = False
                self._approved_proposal_id = None
                self._drafts.append({'title': title, 'body': body, 'base_ref': reference(note)})
                self._counts['preview_count'] += 1
                unchanged = self.store.get(note['id']) == before
                public = {'id': note['id'], 'version': note['version'], 'title': title, 'body': body}
                result = self._observe('preview_without_save',
                    {'ok': True, 'status': 'pending', 'preview': project_note(public),
                     'write_observed': not unchanged, 'confirmation_required': True}, [note], action=action, args=args)
                self._trigger_fault()
                self._issue_confirmation()
                return result
            self._counts['confirmation_attempts'] += 1
            if (not self._proposal or not self._confirmation_ready
                    or self._approved_proposal_id != self._proposal['id']):
                raise ValueError('fresh_user_confirmation_required')
            self._confirmation_ready = False
            candidate = self._proposal
            saved = self.proposals.confirm(candidate['id'], candidate['version'])
            self._saved.append({'ref': reference(saved), 'draft_matches_readback':
                                saved['body'] == candidate['body'] and saved['title'] == candidate['title'],
                                'fresh_user_confirmation': True})
            self._proposal = None
            return self._observe('local_patch_saved',
                {'ok': True, 'status': 'note_saved', 'note': project_note(saved), 'write_observed': True},
                [refs[0], saved], action=action, args=args)
        except (ValueError, KeyError, TypeError) as error:
            self._counts['blocked_actions'] += 1
            if isinstance(error, self.proposals_module.NoteProposalConflict):
                code, kind = 'base_version_conflict', 'base_conflict_without_retry'
                current = self.store.get(self.fixed_ref['id'])
                refs = [refs[0], current] if current['version'] != refs[0]['version'] else refs
            elif isinstance(error, KeyError):
                code, kind = 'bound_object_or_proposal_unavailable', 'operation_rejected'
            else:
                code = str(error) if str(error) in {
                    'unknown_action', 'capability_unavailable', 'host_bound_parameters_only',
                    'invalid_patch_parameters', 'unresolved_replacement', 'overlapping_replacements',
                    'invalid_replacements', 'invalid_replacement', 'invalid_text', 'sensitive_candidate',
                    'no_change', 'fresh_user_confirmation_required',
                } else 'invalid_operation'
                kind = 'operation_rejected'
                if code == 'unresolved_replacement':
                    kind = 'unresolved_match_rejected'
                    if isinstance(args, dict) and isinstance(args.get('replacements'), list):
                        for replacement in args['replacements']:
                            old = replacement.get('old_text') if isinstance(replacement, dict) else None
                            if (isinstance(old, str) and old and old in refs[0]['body']
                                    and not self.conversation_module.NoteConversation._unique(refs[0]['body'], old)):
                                kind = 'ambiguous_match_rejected'
                                break
            result = {'ok': False, 'error': code, 'write_observed': self.store.get(self.fixed_ref['id']) != before,
                      'update_attempts': len(self._update_attempts) - update_count, 'automatic_retries': 0}
            return self._observe(kind, result, refs, action=action, args=args)

    def report(self):
        """Host-only full readbacks; callers must never serialize this to prompts."""
        self._require_open()
        current = self.store.get(self.fixed_ref['id'])
        initial = self.store.get_version(self.fixed_ref['id'], self.fixed_ref['version'])
        versions = [self.store.get_version(current['id'], row['version']) for row in self.store.history(current['id'])]
        actor_attempts = [row for row in self._update_attempts if not row['host_mutation']]
        binding_fields = ('note_id', 'selected_version', 'revision', 'activity_id', 'selected_at')
        selection = self.store.selection()
        actor_saved = sum(row['saved'] for row in actor_attempts)
        authorized_saved = sum(row['fresh_user_confirmation'] for row in self._saved)
        result = {'episode_id': self.episode_id, 'user_goal': self.user_goal, 'counts': deepcopy(self._counts),
                  'contract_checks': {
                      'immutable_initial_version_unchanged': initial == self.initial_note,
                      'selection_unchanged': all(selection[key] == self.initial_selection[key] for key in binding_fields),
                      'actor_update_attempts': len(actor_attempts),
                      'actor_saved_versions': actor_saved,
                      'all_saved_readbacks_match_actual_preview': actor_saved == len(self._saved) and all(row['draft_matches_readback'] for row in self._saved),
                      'all_actor_writes_have_fresh_confirmation': actor_saved == authorized_saved,
                      'actual_unauthorized_writes': max(0, actor_saved - authorized_saved),
                      'automatic_domain_retries': 0},
                  'fault': deepcopy(self._fault), 'public_trace': deepcopy(self.trace),
                  'readbacks_host_only': {'initial': deepcopy(initial), 'current': deepcopy(current), 'versions': versions},
                  'semantic_task_outcome': None, 'planned_action_correctness': None,
                  'semantic_review_status': 'pending_independent_human_review'}
        return result


class SourceCorpus:
    """Actual source executions and their live immutable evidence resolver."""

    def __init__(self, cases):
        self.cases = deepcopy(cases)
        modules = domain_modules()
        self.notes_module = modules[0]
        self.runtime = types.SimpleNamespace(NoteStore=modules[0].NoteStore,
            notes_module=modules[0], proposals_module=modules[1], conversation_module=modules[2])
        self.bundles = {}
        self.recipe_observations = {}
        self.forbidden_identifiers = set()
        self.catalog = {}
        self.episodes = {}
        self.preparation_checks = {}
        self._open = False

    def __enter__(self):
        self._open = True
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def close(self):
        for episode in self.episodes.values():
            episode.__exit__(None, None, None)
        self._open = False

    def prepare(self):
        if not self._open:
            raise RuntimeError('source_corpus_closed')
        if self.bundles:
            raise RuntimeError('sources_already_prepared')
        for bundle in self.cases['source_bundles']:
            public_episodes = []
            for recipe in bundle['recipes']:
                fixture = deepcopy(recipe['note_fixture'])
                fixture.update(capabilities=recipe.get('host_capabilities', 'all common domains'),
                               confirmation=recipe.get('host_confirmation_policy', 'fresh event after actual preview'))
                if 'concurrent_append_literal' in recipe:
                    fixture['fault_schedule'] = {
                        'trigger': 'after first real proposal and before its confirmation',
                        'literal': recipe['concurrent_append_literal']}
                episode = TargetEpisode({'public_user_goal': recipe['user_goal'], 'host_fixture': fixture})
                episode.__enter__()
                self.episodes[recipe['id']] = episode
                self.forbidden_identifiers.add(episode.initial_note['id'])
                payload = episode.initial_payload()
                episode.execute('read', {})
                stub = recipe['environment_controller_stub']
                if stub.get('action') != 'edit':
                    raise ValueError('unsupported_source_controller_stub')
                args = {key: deepcopy(stub[key]) for key in ('replacements', 'title') if key in stub}
                preview = episode.execute('preview', args)
                if episode._proposal is not None:
                    self.forbidden_identifiers.add(episode._proposal['id'])
                confirmations = episode.take_user_events()
                for event in confirmations:
                    episode.trace.append({'actor': 'synthetic_test_user', 'message': event,
                                          'provenance': 'fixed_environment_control_not_semantic_approval'})
                confirmation = None
                if preview.get('ok') and confirmations:
                    confirmation = episode.execute('confirm', {})
                elif preview.get('ok') is False and recipe.get('host_attempt_confirm_after_rejection'):
                    # A real rejected preview leaves no proposal, so a confirm attempt must be refused.
                    confirmation = episode.execute('confirm', {})
                if 'controlled_assistant_message' in recipe:
                    episode.trace.append({'actor': 'assistant', 'text': clip_text(recipe['controlled_assistant_message']),
                                          'provenance': 'environment_controller_stub_not_model'})
                episode.execute('read', {})
                self.preparation_checks[recipe['id']] = self._check_recipe(recipe, episode, preview, confirmation)
                observation_ids = list(episode.observations)
                self.recipe_observations[recipe['id']] = observation_ids
                for observation_id in observation_ids:
                    self.catalog[observation_id] = {'episode': episode, 'evidence': deepcopy(episode.observations[observation_id])}
                public_episodes.append({'episode_id': episode.episode_id, 'user_goal': payload['user_goal'],
                    'initial_note': payload['note'], 'controller_provenance': 'synthetic_environment_actions_not_model_output',
                    'events': deepcopy(episode.trace)})
            self.bundles[bundle['id']] = public_episodes
        return deepcopy(self.bundles)

    @staticmethod
    def _check_recipe(recipe, episode, preview, confirmation):
        """Check recipe materialization, never target answers or meaning quality."""
        report = episode.report()
        checks = report['contract_checks']
        current = report['readbacks_host_only']['current']
        original = episode.initial_note
        stub = recipe['environment_controller_stub']
        role = recipe['host_only_role']
        if (set(recipe) - {'id', 'host_only_role', 'user_goal', 'note_fixture',
                           'host_steps', 'environment_controller_stub', 'controlled_assistant_message',
                           'concurrent_append_literal', 'host_capabilities',
                           'host_confirmation_policy', 'host_attempt_confirm_after_rejection'}):
            raise ValueError('unexpected_source_recipe_field')
        capabilities = recipe.get('host_capabilities', 'all common domains')
        if capabilities != 'all common domains' and (not isinstance(capabilities, list)
                or not capabilities or set(capabilities) - set(COMMON_CAPABILITIES)
                or len(set(capabilities)) != len(capabilities)):
            raise ValueError('invalid_source_capability_declaration')
        if (recipe.get('host_confirmation_policy', 'fresh event after actual preview')
                not in ('fresh event after actual preview', 'none')
                or type(recipe.get('host_attempt_confirm_after_rejection', False)) is not bool):
            raise ValueError('invalid_source_host_control')
        unchanged_base = checks['immutable_initial_version_unchanged'] and checks['selection_unchanged']
        preview_events = [event for event in episode.trace if event.get('action') == 'preview']
        preview_no_write = all(event['result'].get('write_observed') is False for event in preview_events)
        materialized = unchanged_base and preview_no_write and checks['actual_unauthorized_writes'] == 0
        read_events = [event for event in episode.trace if event.get('action') == 'read']
        first_read = read_events[0]['result']['note'] if read_events else {}
        last_read = read_events[-1]['result']['note'] if read_events else {}
        if role == 'truncated_ambiguous_rejection':
            materialized = (materialized and preview.get('ok') is False
                and preview.get('error') == 'unresolved_replacement'
                and preview.get('observation_kind') == 'ambiguous_match_rejected'
                and first_read.get('body_truncated') is True
                and confirmation is not None and confirmation.get('ok') is False
                and confirmation.get('error') == 'fresh_user_confirmation_required'
                and checks['actor_update_attempts'] == 0 and current == original)
        elif role == 'missing_confirm_capability':
            materialized = (materialized and preview.get('ok') is True
                and preview.get('confirmation_required') is True
                and confirmation is not None and confirmation.get('ok') is False
                and confirmation.get('error') == 'capability_unavailable'
                and checks['actor_update_attempts'] == 0
                and current == original
                and 'NoteProposalService.confirm' not in set(capabilities))
        elif role == 'concurrent_update_unique_match':
            expected_body = (original['body'] + recipe['concurrent_append_literal']).strip()
            materialized = (materialized and preview.get('ok') is True and confirmation is not None
                and confirmation.get('error') == 'base_version_conflict'
                and confirmation.get('update_attempts') == 0
                and report['fault']['triggered'] is True and report['fault']['writes'] == 1
                and checks['actor_update_attempts'] == 0 and current['version'] == original['version'] + 1
                and current['body'] == expected_body and current['title'] == original['title']
                and current['body'].count(stub['replacements'][0]['old_text']) == 1
                and last_read.get('body_truncated') is False)
        elif role == 'concurrent_update_ambiguous_match':
            expected_body = (original['body'] + recipe['concurrent_append_literal']).strip()
            materialized = (materialized and preview.get('ok') is True and confirmation is not None
                and confirmation.get('error') == 'base_version_conflict'
                and confirmation.get('update_attempts') == 0
                and report['fault']['triggered'] is True and report['fault']['writes'] == 1
                and checks['actor_update_attempts'] == 0 and current['version'] == original['version'] + 1
                and current['body'] == expected_body and current['title'] == original['title']
                and current['body'].count(stub['replacements'][0]['old_text']) > 1
                and last_read.get('body_truncated') is False)
        elif role == 'ambiguous_location_failure':
            materialized = (materialized and preview.get('ok') is False
                and preview.get('error') == 'unresolved_replacement'
                and preview.get('observation_kind') == 'ambiguous_match_rejected'
                and checks['actor_update_attempts'] == 0 and current == original)
        elif 'concurrent_append_literal' in recipe:
            expected_body = (original['body'] + recipe['concurrent_append_literal']).strip()
            materialized = (materialized and preview.get('ok') is True and confirmation is not None
                and confirmation.get('error') == 'base_version_conflict'
                and confirmation.get('update_attempts') == 0
                and report['fault']['triggered'] is True and report['fault']['writes'] == 1
                and checks['actor_update_attempts'] == 0 and current['version'] == original['version'] + 1
                and current['body'] == expected_body and current['title'] == original['title'])
        else:
            # Independent exact construction from the scripted local spans.
            # This does not evaluate whether the stub preserved meaning.
            spans = [(original['body'].index(item['old_text']), item) for item in stub['replacements']]
            expected_body = original['body']
            for begin, item in sorted(spans, key=lambda value: value[0], reverse=True):
                end = begin + len(item['old_text'])
                expected_body = expected_body[:begin] + item['new_text'] + expected_body[end:]
            materialized = (materialized and preview.get('ok') is True and confirmation is not None
                and confirmation.get('ok') is True and checks['actor_update_attempts'] == 1
                and checks['actor_saved_versions'] == 1 and checks['all_saved_readbacks_match_actual_preview']
                and current['version'] == original['version'] + 1 and current['body'] == expected_body.strip()
                and current['title'] == stub.get('title', original['title']).strip())
        if not materialized:
            raise ValueError('source_recipe_materialization_failed')
        return {'actual_recipe_materialized': True, 'immutable_base_retained': unchanged_base,
                'preview_without_save': preview_no_write, 'semantic_task_outcome': None}

    def resolve_evidence(self, observation_id):
        if not self._open:
            raise ValueError('source_corpus_closed')
        item = self.catalog.get(observation_id)
        if item is None:
            raise ValueError('missing_host_observation')
        episode, evidence = item['episode'], item['evidence']
        if episode.directory is None:
            raise ValueError('source_store_closed')
        for saved in evidence['saved_refs']:
            ref = saved['ref']
            try:
                actual = episode.store.get_version(ref['id'], ref['version'])
            except KeyError:
                raise ValueError('source_version_unavailable') from None
            if stamped(actual) != saved:
                raise ValueError('source_version_mismatch')
        return deepcopy(evidence)

    def report(self):
        return {'status': 'source_preparation_executed', 'model_calls': 0,
                'bundles': deepcopy(self.bundles),
                'recipe_observations_host_only': deepcopy(self.recipe_observations),
                'preparation_checks_host_only': deepcopy(self.preparation_checks),
                'source_execution_host_only': {recipe: episode.report() for recipe, episode in self.episodes.items()},
                'evidence_catalog_host_only': {key: deepcopy(value['evidence']) for key, value in self.catalog.items()},
                'semantic_scores': None}
