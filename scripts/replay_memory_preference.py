"""Replay recorded public extraction responses through isolated current code.

No model request, credentials, dotenv or user database is used. The original
report is immutable evidence; the new report measures changed program behavior
on those frozen responses, not the revised prompt's model accuracy.
"""
from __future__ import annotations

import argparse
import asyncio
import copy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import eval_memory


class RecordedProvider:
    def __init__(self, source):
        self.responses = {}
        self.replayed = 0
        for case in source['cases']:
            for step in case['execution']:
                result = step.get('provider_result')
                if result is None:
                    continue
                message = step['saved_user_message']
                identity = (message['id'], message['text'])
                if (identity in self.responses or result.get('raw_truncated')
                        or not isinstance(result.get('raw'), str)):
                    raise ValueError('ambiguous_or_incomplete_recorded_response')
                self.responses[identity] = result
        if len(self.responses) != 12:
            raise ValueError('expected_twelve_complete_recorded_responses')

    async def complete_raw(self, messages, **options):
        user = json.loads(messages[1]['content'])['user_messages']
        if len(user) != 1 or options.get('max_attempts') != 1:
            raise ValueError('unexpected_replay_input')
        result = self.responses.pop((user[0]['id'], user[0]['text']))
        self.replayed += 1
        # Prior billed usage is never counted as new replay usage.
        return {'text': result['raw'], 'finish_reason': result['finish_reason'], 'usage': {}}


def replay_runtime(source):
    runtime = eval_memory.load_runtime()
    recorded_messages = {}
    for case in source['cases']:
        for step in case['execution']:
            message = step.get('saved_user_message')
            if message is not None:
                recorded_messages[(message['session_id'], message['id'])] = message

    class ReplayStore(runtime.MemoryStore):
        def add_message(self, session_id, role, text, mode='text'):
            identifier = super().add_message(session_id, role, text, mode)
            original = recorded_messages.get((session_id, identifier))
            if role == 'user':
                if original is None or original['text'] != text or original['role'] != role:
                    raise ValueError('replay_message_does_not_match_original')
                self._write('UPDATE messages SET created_at = ? WHERE id = ?',
                            (original['created_at'], identifier))
            return identifier

    return SimpleNamespace(MemoryStore=ReplayStore, MemoryManager=runtime.MemoryManager)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(argv)
    raw = args.source.read_bytes()
    source = json.loads(raw)
    if source.get('mode') != 'live' or source.get('status') != 'complete' or len(source.get('cases', [])) != 12:
        raise ValueError('expected_complete_first_live_report')
    cases = [{field: copy.deepcopy(case[field]) for field in
              ('id', 'max_extraction_calls', 'steps', 'queries', 'expected')} for case in source['cases']]
    for case in cases:
        if case['id'] == 'ambiguous_conflict':
            case['queries'] += ['红茶', '最近喜欢的茶']
            case['expected'] = {'review': '长期最喜欢绿茶与最近也喜欢红茶应共存；红茶应可检索，保留原话日期和程度，不覆盖旧最爱。'}
    report = eval_memory.make_report(cases, mode='offline_replay', provider='recorded_public_responses',
                                     model=source['model'], reasoning_effort=source.get('reasoning_effort', ''))
    report.update(network_calls=0, source_report=args.source.name,
                  source_sha256=hashlib.sha256(raw).hexdigest())
    report['notes'] = [
        'Offline replay only: every provider result is frozen public text from the source report; no network model call.',
        'The model field describes the historical request, not a newly executed model.',
        'Budget call counters during checkpoints count local replay invocations; billed usage is empty.',
        'Original user-message timestamps are retained; new temporary SQLite databases use current schema.',
        'Red-tea and recent-preference queries are added in this report only; the original report is unchanged.',
        'This checks program behavior on recorded outputs, not revised-prompt model stability or semantic accuracy.',
        'All manual_review fields remain null. No hidden reasoning, credentials or real user database is used.',
    ]
    writer = eval_memory.ReportWriter(args.output or eval_memory.ROOT / 'eval/research' /
                                      f'memory_preference_replay_20261013_{uuid4().hex[:8]}.json')
    provider = RecordedProvider(source)
    runtime = replay_runtime(source)
    # An accidental fallback to either the default LLM or a real transport fails.
    import httpx
    with patch('server.llm.llm.complete_raw', side_effect=AssertionError('network_model_forbidden')):
        with patch.object(httpx.AsyncHTTPTransport, 'handle_async_request',
                          side_effect=AssertionError('network_transport_forbidden')):
            asyncio.run(eval_memory.execute_cases(report, writer, provider, runtime=runtime))
    report['replayed_extractions'] = provider.replayed
    report['unconsumed_recorded_responses'] = len(provider.responses)
    if report['status'] == 'complete' and provider.responses:
        report['status'] = 'partial'
    writer.save(report)
    if hashlib.sha256(args.source.read_bytes()).hexdigest() != report['source_sha256']:
        raise RuntimeError('source_changed_during_replay')
    print(json.dumps({'status': report['status'], 'network_calls': 0,
                      'replayed_extractions': provider.replayed, 'output': str(writer.path)}))
    return 0 if report['status'] == 'complete' else 1


if __name__ == '__main__':
    raise SystemExit(main())
