"""Bounded live quality baseline via the production editor insight service.

Default mode only validates the original fixtures and prints the request plan.
--live sends their title/body and discussion turns to the configured model.
No application bootstrap, audio, personal notes, databases, or memory writes.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.context import _text
from server.assistant.editor_insights import (
    DISCUSSION_RULES, INSIGHT_RULES, EditorInsightService,
)
from server.llm import LLM

DEFAULT_CASES = ROOT / 'eval/editor_quality_cases.json'
USAGE_KEYS = ('prompt_tokens', 'completion_tokens', 'total_tokens')


def safe_usage(value):
    if not isinstance(value, dict):
        return {}
    return {key: value[key] for key in USAGE_KEYS
            if type(value.get(key)) is int and value[key] >= 0}


def load_cases(path, selected=()):
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('schema_version') != 1 or not isinstance(data.get('cases'), list):
        raise ValueError('invalid_cases_schema')
    seen = set()
    for case in data['cases']:
        case_id = case.get('id')
        if (not isinstance(case_id, str) or not re.fullmatch(r'[a-z0-9_-]{1,60}', case_id)
                or case_id in seen):
            raise ValueError('invalid_or_duplicate_case_id')
        seen.add(case_id)
        for key in ('genre', 'author_intent', 'title', 'body'):
            if not isinstance(case.get(key), str) or not case[key].strip():
                raise ValueError(f'{case_id}:missing_{key}')
        if len(case['body'].strip()) < 80 or len(case['body']) > 20000 or len(case['title']) > 160:
            raise ValueError(f'{case_id}:invalid_draft_length')
        expectation = case.get('expectation', {})
        if expectation.get('mode') not in ('insight', 'quiet', 'either'):
            raise ValueError(f'{case_id}:invalid_expectation')
        turns = case.get('discussion', [])
        if not isinstance(turns, list) or len(turns) > EditorInsightService.MAX_DISCUSSION_CHECKS:
            raise ValueError(f'{case_id}:invalid_discussion_plan')
        turn_ids = set()
        for turn in turns:
            if (not isinstance(turn, dict) or not isinstance(turn.get('id'), str)
                    or not re.fullmatch(r'[a-z0-9_-]{1,60}', turn['id'])
                    or turn['id'] == 'analyze' or turn['id'] in turn_ids
                    or not isinstance(turn.get('user_text'), str)
                    or not 1 <= len(turn['user_text'].strip()) <= 1000):
                raise ValueError(f'{case_id}:invalid_discussion_turn')
            turn_ids.add(turn['id'])
        for field in [case['title'], case['body'], *(t['user_text'] for t in turns)]:
            if '\x00' in field or _text(field, len(field)) != field:
                raise ValueError(f'{case_id}:fixture_contains_sensitive_data')
    if set(selected) - seen:
        raise ValueError('unknown_selected_case')
    cases = [c for c in data['cases'] if not selected or c['id'] in selected]
    if not cases:
        raise ValueError('no_cases')
    return cases


class RecordingLLM:
    """Record only response content/metadata; never reasoning or credentials."""

    def __init__(self, delegate, max_calls):
        self.delegate = delegate
        self.offline = delegate.offline
        self.model = getattr(delegate, 'model', '')
        self.max_calls = max_calls
        self.calls = []
        self.case_id = ''
        self.stage = ''

    async def complete_raw(self, messages, **kwargs):
        if len(self.calls) >= self.max_calls:
            raise RuntimeError('eval_request_budget_exhausted')
        payload = json.loads(messages[1]['content'])
        allowed = ({'draft', 'dismissed_anchors'} if self.stage == 'analyze' else
                   {'draft', 'insight', 'boundaries'})
        if (set(payload) != allowed or set(payload['draft']) !=
                {'title', 'body', 'body_truncated', 'body_chars', 'saved', 'untrusted'}):
            raise RuntimeError('unexpected_production_payload')
        expected_roles = ['system', 'user']
        if self.stage != 'analyze':
            history = messages[2:-1]
            if len(history) > 2 * EditorInsightService.MAX_DISCUSSION_HISTORY_TURNS or len(history) % 2:
                raise RuntimeError('unexpected_production_history')
            expected_roles.extend(['user', 'assistant'] * (len(history) // 2))
            expected_roles.append('user')
        if ([message.get('role') for message in messages] != expected_roles
                or any(set(message) != {'role', 'content'}
                       or not isinstance(message['content'], str) for message in messages)):
            raise RuntimeError('unexpected_production_messages')
        if kwargs.get('max_attempts') != 1 or kwargs.get('json_mode') is not True:
            raise RuntimeError('unexpected_production_request_options')
        row = {'index': len(self.calls) + 1, 'case_id': self.case_id,
               'stage': self.stage, 'request': dict(kwargs), 'status': 'pending',
               'expectations_excluded': True}
        self.calls.append(row)
        started = time.perf_counter()
        try:
            response = await self.delegate.complete_raw(messages, **kwargs)
            fields = response if isinstance(response, dict) else {}
            raw = fields.get('text')
            valid_text = isinstance(raw, str)
            finish = fields.get('finish_reason')
            row.update(status='response', text=_text(raw, len(raw)) if valid_text else None,
                       invalid_response_shape=not isinstance(response, dict) or not valid_text,
                       response_redacted=valid_text and _text(raw, len(raw)) != raw,
                       finish_reason=_text(finish, 32) if isinstance(finish, str) else '',
                       usage=safe_usage(fields.get('usage')))
            return response
        except asyncio.CancelledError:
            row['status'] = 'cancelled'
            raise
        except Exception as exc:
            row.update(status='error', error_type=type(exc).__name__)
            raise
        finally:
            row['latency_seconds'] = round(time.perf_counter() - started, 3)


def analysis_outcome(result, calls, body):
    """Don't credit rejected/failed model responses as intentional silence."""
    if not calls:
        return result.get('reason', 'no_model_call')
    call = calls[-1]
    if call['status'] != 'response':
        return result.get('reason', 'provider_error')
    if call.get('invalid_response_shape'):
        return 'analysis_invalid_response'
    if call.get('response_redacted'):
        return 'sensitive_response'
    if call.get('finish_reason') == 'length':
        return 'analysis_output_truncated'
    if call.get('finish_reason') not in (None, '', 'stop'):
        return 'analysis_invalid_response'
    if isinstance(call.get('text'), str) and not call['text'].strip():
        return 'analysis_empty_response'
    try:
        parsed = json.loads(call['text'])
    except (json.JSONDecodeError, TypeError):
        return 'invalid_json'
    if parsed == {'action': 'quiet'}:
        return 'model_quiet' if result['status'] == 'quiet' else 'service_model_mismatch'
    if not isinstance(parsed, dict) or set(parsed) != {'action', 'anchor', 'point'} or parsed['action'] != 'insight':
        return 'invalid_schema'
    anchor = parsed['anchor']
    if (not isinstance(anchor, str) or not 5 <= len(anchor) <= 120
            or '\x00' in anchor or not EditorInsightService._unique(body[:4000], anchor)
            or not EditorInsightService._unique(body, anchor)):
        return 'invalid_anchor'
    if not EditorInsightService._validate_insight(parsed, body[:4000], body):
        return 'invalid_point'
    return 'insight' if result['status'] == 'insight' else 'service_model_mismatch'


def summary(report):
    calls, rows = report['calls'], report['cases']
    usage = {key: sum(c.get('usage', {}).get(key, 0) for c in calls) for key in USAGE_KEYS}
    return {
        'request_count': len(calls), 'request_limit': report['request_limit'],
        'planned_request_upper_bound': report['planned_request_upper_bound'],
        'response_count': sum(c['status'] == 'response' for c in calls),
        'calls_with_usage': sum(bool(c.get('usage')) for c in calls),
        'usage_reported_only': usage,
        'unreported_usage_calls': sum(not c.get('usage') for c in calls),
        'finish_reason_length_calls': sum(c.get('finish_reason') == 'length' for c in calls),
        'analysis_outcomes': dict(Counter(c['analysis'].get('outcome', 'evaluation_incomplete') for c in rows)),
        'discussion_outcomes': dict(Counter(t.get('outcome', 'evaluation_incomplete') for c in rows for t in c['discussion'])),
        'request_contract_checks_pass': bool(calls) and all(c.get('expectations_excluded') and
            c['request'].get('max_attempts') == 1 for c in calls),
        'analysis_protocol_accepted': sum(c['analysis']['outcome'] in ('insight', 'model_quiet') for c in rows),
        'semantic_review': 'pending_independent_review',
    }


def save_report(path, report):
    report['summary'] = summary(report)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    for attempt in range(6):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            # Windows readers/indexers can briefly hold a file without delete
            # sharing. Retry the local atomic write only, never a model call.
            if attempt == 5:
                raise
            time.sleep(0.1)


async def run_cases(cases, recorder, output, metadata):
    service = EditorInsightService(recorder, enabled=lambda: True)
    report = {**metadata, 'run_status': 'running', 'calls': recorder.calls, 'cases': []}
    save_report(output, report)
    try:
        for case in cases:
            row = {**case, 'analysis': {'outcome': 'pending'}, 'discussion': []}
            report['cases'].append(row)
            session = 'quality-' + case['id']
            service.state(session, 1, True)
            recorder.case_id, recorder.stage = case['id'], 'analyze'
            start = len(recorder.calls)
            result = await service.analyze(session, 1, case['title'], case['body'], True)
            # The production timeout returns immediately after cancelling its
            # task. Let cooperative cancellation record its final metadata.
            await asyncio.sleep(0)
            row['analysis'] = {'result': result, 'call_indices': list(range(start + 1, len(recorder.calls) + 1)),
                               'outcome': analysis_outcome(result, recorder.calls[start:], case['body'])}
            if result['status'] == 'insight':
                point = result['insight']['point']
                first_sentence = re.split(r'[。！？!?\n]', point, maxsplit=1)[0]
                row['analysis']['presentation'] = {'first_sentence_chars': len(first_sentence),
                    'first_sentence_at_most_24': len(first_sentence) <= 24,
                    'teaser': result['insight']['teaser']}
            save_report(output, report)
            print(case['id'], row['analysis']['outcome'], flush=True)
            for turn in case.get('discussion', []):
                turn_row = {**turn, 'outcome': 'pending'}
                row['discussion'].append(turn_row)
                if result['status'] != 'insight':
                    turn_row.update(outcome='skipped_no_insight', call_indices=[])
                    continue
                recorder.stage = turn['id']
                start = len(recorder.calls)
                try:
                    reply = await service.reply(result['insight']['id'], session, 1, turn['user_text'])
                    turn_row.update(outcome='reply', result=reply)
                except Exception as exc:
                    turn_row.update(outcome='discussion_error', error_type=type(exc).__name__,
                                    reason=getattr(exc, 'reason', 'discussion_failed'))
                await asyncio.sleep(0)
                turn_row['call_indices'] = list(range(start + 1, len(recorder.calls) + 1))
                save_report(output, report)
                print(' ', turn['id'], turn_row['outcome'], flush=True)
            # Each case has its own actual session. Release it promptly.
            service.state(session, 1, False)
            save_report(output, report)
        report.update(run_status='completed', completed_at=datetime.now(timezone.utc).isoformat())
        return report
    except BaseException as exc:
        report.update(run_status='cancelled' if isinstance(exc, asyncio.CancelledError) else 'failed',
                      interruption_type=type(exc).__name__)
        raise
    finally:
        service.close()
        await asyncio.sleep(0)
        for call in recorder.calls:
            if call['status'] == 'pending':
                # Do not wait indefinitely for a provider that ignores cancel.
                # A request may have been billed even without a final response.
                call['status'] = 'response_unavailable_at_shutdown'
        for row in report['cases']:
            if row['analysis'].get('outcome') == 'pending':
                row['analysis']['outcome'] = 'evaluation_incomplete'
            for turn in row['discussion']:
                if turn['outcome'] == 'pending':
                    turn['outcome'] = 'evaluation_incomplete'
        save_report(output, report)


async def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Run the configured real model')
    parser.add_argument('--cases', type=Path, default=DEFAULT_CASES)
    parser.add_argument('--case', action='append', default=[], help='Select a case ID (repeatable)')
    parser.add_argument('--max-calls', type=int, default=15)
    parser.add_argument('--output', type=Path, help='New result file; existing files are preserved')
    args = parser.parse_args(argv)
    if not 1 <= args.max_calls <= 30:
        parser.error('--max-calls must be between 1 and 30')
    cases = load_cases(args.cases, args.case)
    planned = sum(1 + len(c.get('discussion', [])) for c in cases)
    if planned > args.max_calls:
        parser.error(f'planned upper bound {planned} exceeds --max-calls {args.max_calls}')
    print(json.dumps({'mode': 'live' if args.live else 'dry_run', 'case_count': len(cases),
                      'request_upper_bound': planned, 'request_limit': args.max_calls,
                      'fixtures': 'original_synthetic_not_user_drafts'}, ensure_ascii=False), flush=True)
    if not args.live:
        return 0
    # The client timeout has to sit above the service ceiling, or the harness
    # cuts a turn off before production would and reports a failure the author
    # never sees. One live round lost a turn at 25s while the service was
    # already allowed 45s.
    llm = LLM(request_timeout=EditorInsightService.MODEL_TIMEOUT_SECONDS + 5)
    try:
        if llm.offline:
            raise RuntimeError('live_model_required')
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        output = args.output or ROOT / 'eval/editor_quality' / stamp / 'results.json'
        output.parent.mkdir(parents=True, exist_ok=True)
        # Reserve before any billable call. Never overwrite an existing baseline.
        with output.open('x', encoding='utf-8') as reserved:
            reserved.write('{}\n')
        source_hash = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        metadata = {'schema_version': 1, 'mode': 'live', 'started_at': datetime.now(timezone.utc).isoformat(),
            'provider': llm.provider, 'model': llm.model,
            'fixtures': 'original_synthetic_not_user_drafts', 'case_source_sha256': source_hash(args.cases),
            'runner_sha256': source_hash(Path(__file__)),
            'service_sha256': source_hash(ROOT / 'server/assistant/editor_insights.py'),
            'rubric_sha256': source_hash(ROOT / 'docs/EDITOR_QUALITY_RUBRIC.md'),
            'insight_rules_sha256': hashlib.sha256(INSIGHT_RULES.encode()).hexdigest(),
            'discussion_rules_sha256': hashlib.sha256(DISCUSSION_RULES.encode()).hexdigest(),
            'prompt_snapshot': {'insight': INSIGHT_RULES, 'discussion': DISCUSSION_RULES},
            'model_options': {'frequency_penalty': llm.settings.llm_frequency_penalty,
                              'presence_penalty': llm.settings.llm_presence_penalty,
                              'reasoning_effort': llm.settings.llm_reasoning_effort,
                              'request_timeout_seconds': llm.request_timeout},
            'request_limit': args.max_calls, 'planned_request_upper_bound': planned,
            'constraints': {'production_service_path': True, 'audio_calls': 0,
                            'personal_drafts_read': 0, 'production_database_access': False,
                            'review_expectations_sent_to_model': False, 'internal_retries': 0}}
        report = await run_cases(cases, RecordingLLM(llm, args.max_calls), output, metadata)
        print(json.dumps({'output': str(output.resolve()), **report['summary']}, ensure_ascii=False), flush=True)
        failures = set(report['summary']['analysis_outcomes']) - {'insight', 'model_quiet'}
        return int(bool(failures) or report['summary']['discussion_outcomes'].get('discussion_error', 0) > 0)
    finally:
        await llm.aclose()


if __name__ == '__main__':
    raise SystemExit(asyncio.run(main()))
