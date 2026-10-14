"""Plan-first memory evaluation with synthetic inputs and bounded live opt-in.

Default invocation writes a plan only. --live requires explicit provider, URL,
model and a nonblank key in --api-key-env (default MEMORY_EVAL_API_KEY).
Never loads .env, uses a production database, seeds expected memories, retries
an extraction or persists hidden reasoning. Expectations stay out of prompts.
"""
from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import sys
import tempfile
from types import SimpleNamespace
from urllib.parse import urlsplit
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
CASE_PATH = ROOT / 'eval' / 'research' / 'memory_live_cases.json'
MAX_CALLS = 12
MAX_OUTPUT_TOKENS = 800
REQUEST_TIMEOUT_SECONDS = 45
REPETITIONS = 1
REASONING_EFFORTS = ('', 'none', 'minimal', 'low', 'medium', 'high', 'xhigh')
MAX_PUBLIC_CHARS = 20000
_CREDENTIAL = re.compile(
    r'(?i)(?:\bBearer\s+\S+|\b(?:sk-[A-Za-z0-9_-]{8,}|gh[pousr]_[A-Za-z0-9_]+)|'
    r'''(?:api[_ -]?key|password|secret|authorization|密码|口令|密钥|令牌)["'\s]*[:=：]\s*\S+)'''
)
_USAGE_KEYS = frozenset({
    'prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens',
    'cached_tokens', 'reasoning_tokens', 'audio_tokens', 'accepted_prediction_tokens',
    'rejected_prediction_tokens', 'prompt_tokens_details', 'completion_tokens_details',
    'input_tokens_details', 'output_tokens_details',
})


class EvaluationError(RuntimeError):
    """Errors are fixed codes, never provider bodies or credential values."""


def numeric_usage(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for key in _USAGE_KEYS:
        number = value.get(key)
        if isinstance(number, dict):
            nested = numeric_usage(number)
            if nested:
                result[key] = nested
        elif type(number) in (int, float) and math.isfinite(number) and number >= 0:
            result[key] = number
    return result


def safe_report(value, secret=''):
    """Allow public data, redact credential-looking fields and the live key."""
    if isinstance(value, str):
        if (secret and secret in value) or _CREDENTIAL.search(value):
            return '[REDACTED: credential]'
        return value
    if isinstance(value, list):
        return [safe_report(item, secret) for item in value]
    if isinstance(value, dict):
        return {safe_report(str(key), secret): safe_report(item, secret) for key, item in value.items()}
    return value


def load_cases(path=CASE_PATH):
    payload = json.loads(Path(path).read_text(encoding='utf-8'))
    cases = payload.get('cases', [])
    if (payload.get('provenance') != 'authored_synthetic_evaluation' or len(cases) != 12
            or len({case.get('id') for case in cases}) != 12):
        raise EvaluationError('invalid_fixed_cases')
    total = 0
    for case in cases:
        limit = case.get('max_extraction_calls')
        if type(limit) is not int or not 0 <= limit <= 2:
            raise EvaluationError('invalid_case_budget')
        total += limit
        if not case.get('steps') or not case.get('queries') or not isinstance(case.get('expected'), dict):
            raise EvaluationError('invalid_case_shape')
        references = set()
        for step in case['steps']:
            action = step.get('action')
            if action not in ('turn', 'message', 'replay', 'forget_all', 'memory_toggle'):
                raise EvaluationError('invalid_case_action')
            if action == 'turn':
                if not isinstance(step.get('user'), str) or not 0 < len(step['user']) <= 12000:
                    raise EvaluationError('invalid_synthetic_message')
                if 'as' in step:
                    if step['as'] in references:
                        raise EvaluationError('duplicate_message_reference')
                    references.add(step['as'])
            if action == 'message' and (step.get('role') != 'assistant' or not isinstance(step.get('text'), str)):
                raise EvaluationError('invalid_assistant_fixture')
            if action == 'replay' and step.get('ref') not in references:
                raise EvaluationError('missing_message_reference')
            if action == 'memory_toggle' and type(step.get('enabled')) is not bool:
                raise EvaluationError('invalid_memory_toggle')
        if any(not isinstance(query, str) or not query for query in case['queries']):
            raise EvaluationError('invalid_query')
    if total > MAX_CALLS:
        raise EvaluationError('case_budget_exceeded')
    return cases


def make_report(cases, *, mode='plan', provider=None, model=None, reasoning_effort=''):
    if reasoning_effort not in REASONING_EFFORTS:
        raise EvaluationError('invalid_reasoning_effort')
    return {
        'version': 1, 'mode': mode, 'status': 'planned' if mode == 'plan' else 'running',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'provider': provider, 'model': model, 'reasoning_effort': reasoning_effort, 'manual_review': None,
        'budget': {
            'max_extraction_calls': MAX_CALLS, 'max_output_tokens_per_call': MAX_OUTPUT_TOKENS,
            'requested_output_token_bound': MAX_CALLS * MAX_OUTPUT_TOKENS,
            'max_attempts_per_call': 1, 'repetitions': REPETITIONS,
            'request_timeout_seconds': REQUEST_TIMEOUT_SECONDS,
            'calls_reserved': 0, 'calls_completed': 0, 'usage_reported_calls': 0, 'usage_totals': {},
        },
        'notes': [
            'Default plan makes no model, database or dotenv calls. Only --live opts into provider calls.',
            'All cases are authored synthetic data. Expectations never enter extraction prompts or seed memories.',
            'Each case uses its own temporary SQLite database, saved messages, real manager/extractor/store/retriever and a database reopen.',
            'The bound covers requests and requested output tokens, not money, prompt tokens or unreported provider usage. Provider enforcement of max_tokens is required.',
            'Public response text, parsed candidates, drop diagnostics, actual write rows and retrieval are evidence; manual semantic review remains null.',
            'Hidden reasoning, credentials and arbitrary provider/exception bodies are not report fields.',
        ],
        'cases': [{**case, 'status': 'planned', 'manual_review': None, 'execution': []} for case in cases],
    }


class ReportWriter:
    """Reserve a new path exclusively; atomically checkpoint our own report."""
    def __init__(self, path, *, secret=''):
        self.path = Path(path).resolve()
        self.secret = secret
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.open('x', encoding='utf-8').close()
        except FileExistsError:
            raise EvaluationError('output_exists') from None

    def save(self, report):
        temporary = self.path.with_name(f'.{self.path.name}.{uuid4().hex}.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(safe_report(report, self.secret), stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            if temporary.exists():
                temporary.unlink()


def load_runtime():
    """Import application singletons only with an isolated blank configuration."""
    from unittest.mock import patch
    sys.path.insert(0, str(ROOT))
    with patch.dict(os.environ, {'DB_PATH': ':memory:', 'LLM_PROVIDER': 'mock', 'LLM_API_KEY': ''}, clear=True):
        with patch('dotenv.load_dotenv', return_value=False):
            from server.config import settings
            if settings.db_path != ':memory:' or settings.llm_provider != 'mock' or settings.llm_api_key:
                raise EvaluationError('runtime_not_isolated')
            from server.llm import LLM
            from server.memory.manager import MemoryManager
            from server.memory.store import MemoryStore
    return SimpleNamespace(LLM=LLM, MemoryManager=MemoryManager, MemoryStore=MemoryStore)


def live_configuration(args):
    provider = (args.provider or '').strip()
    model = (args.model or '').strip()
    base_url = (args.base_url or '').strip()
    if not provider or provider.lower() == 'mock' or not model or not base_url:
        raise EvaluationError('live_requires_explicit_provider_url_model')
    reasoning_effort = getattr(args, 'reasoning_effort', '')
    if reasoning_effort not in REASONING_EFFORTS:
        raise EvaluationError('invalid_reasoning_effort')
    url = urlsplit(base_url)
    if (url.scheme != 'https' or not url.hostname or url.username or url.password or url.query or url.fragment):
        raise EvaluationError('live_requires_https_without_embedded_credentials')
    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', args.api_key_env):
        raise EvaluationError('invalid_api_key_env_name')
    # This is the only credential source; there is no .env or LLM_API_KEY fallback.
    key = os.environ.get(args.api_key_env, '').strip()
    if not key:
        raise EvaluationError('live_key_missing')
    return SimpleNamespace(
        llm_provider=provider, llm_base_url=base_url.rstrip('/'),
        llm_api_key=key, llm_model=model, llm_temperature=.1,
        llm_max_tokens=MAX_OUTPUT_TOKENS, llm_reasoning_effort=reasoning_effort,
        llm_frequency_penalty=0, llm_presence_penalty=0,
    )


def public_result(result):
    raw = result.get('text') if isinstance(result.get('text'), str) else ''
    reason = result.get('finish_reason')
    return {
        'raw': raw[:MAX_PUBLIC_CHARS], 'raw_truncated': len(raw) > MAX_PUBLIC_CHARS,
        'finish_reason': reason if reason in ('stop', 'length', 'content_filter', 'tool_calls', 'function_call') else 'unknown',
        'usage': numeric_usage(result.get('usage')),
    }


class BudgetedProvider:
    def __init__(self, provider, report, writer):
        self.provider = provider
        self.report = report
        self.writer = writer
        self.case_count = 0
        self.case_limit = 0
        self.record = None

    def begin_case(self, case):
        self.case_count = 0
        self.case_limit = case['max_extraction_calls']

    async def complete_raw(self, messages, **options):
        budget = self.report['budget']
        if budget['calls_reserved'] >= MAX_CALLS or self.case_count >= self.case_limit:
            self.record['budget_failure'] = 'extraction_budget_exceeded'
            raise EvaluationError('extraction_budget_exceeded')
        if options.get('max_tokens') != MAX_OUTPUT_TOKENS or options.get('max_attempts') != 1:
            self.record['budget_failure'] = 'unexpected_extraction_budget'
            raise EvaluationError('unexpected_extraction_budget')
        self.case_count += 1
        budget['calls_reserved'] += 1
        self.record['call_reserved'] = True
        self.writer.save(self.report)  # Preserve the reservation before any network activity.
        result = await asyncio.wait_for(
            self.provider.complete_raw(messages, **{**options, 'model': self.report['model'],
                                                    'reasoning_effort': self.report.get('reasoning_effort', '')}),
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        public = public_result(result)
        self.record['provider_result'] = public
        budget['calls_completed'] += 1
        if public['usage']:
            budget['usage_reported_calls'] += 1
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens', 'input_tokens', 'output_tokens'):
            if key in public['usage']:
                budget['usage_totals'][key] = budget['usage_totals'].get(key, 0) + public['usage'][key]
        self.writer.save(self.report)
        if any(public['usage'].get(key, 0) > MAX_OUTPUT_TOKENS
               for key in ('completion_tokens', 'output_tokens')):
            self.record['budget_failure'] = 'provider_exceeded_requested_output_tokens'
            raise EvaluationError('provider_exceeded_requested_output_tokens')
        # Hidden reasoning is never forwarded into an extraction trace.
        return {'text': result.get('text') if isinstance(result.get('text'), str) else '',
                'finish_reason': public['finish_reason'], 'usage': public['usage']}


def trace_result(trace):
    return {
        'raw': trace.get('raw', '')[:MAX_PUBLIC_CHARS],
        'raw_truncated': len(trace.get('raw', '')) > MAX_PUBLIC_CHARS,
        'parsed': trace.get('parsed', []), 'drops': trace.get('drops', []),
        'usage': numeric_usage(trace.get('usage')), 'finish_reason': trace.get('finish_reason'),
        'elapsed_ms': trace.get('elapsed_ms'), 'manager_skip': trace.get('manager_skip'),
        'error_type': trace.get('error_type'),
    }


def retrieve(manager, queries):
    results = []
    for query in queries:
        hits = manager.retriever.search(query, subject='user')
        context, found = manager.context_with_status(query, subject='user')
        results.append({'query': query, 'hits': [{'score': score, 'row': row.to_dict()} for row, score in hits],
                        'context': context, 'found_context': found})
    return results


async def execute_cases(report, writer, provider, *, runtime=None):
    """Tests may inject a fake provider; all storage and extraction remain real."""
    try:
        return await _execute_cases(report, writer, provider, runtime=runtime)
    except Exception as exc:
        report['status'] = 'partial'
        report['error_type'] = type(exc).__name__
        writer.save(report)
        return report


async def _execute_cases(report, writer, provider, *, runtime=None):
    runtime = runtime or load_runtime()
    budgeted = BudgetedProvider(provider, report, writer)
    with tempfile.TemporaryDirectory(prefix='neurosama-memory-eval-') as directory:
        for index, case in enumerate(report['cases']):
            budgeted.begin_case(case)
            database_path = Path(directory) / f'case-{index}.sqlite'
            store = runtime.MemoryStore(str(database_path))
            manager = runtime.MemoryManager(store)
            session = store.start_session(f'synthetic-{case["id"]}')
            references = {}
            case['status'] = 'running'
            failed = False
            try:
                for step in case['steps']:
                    record = {'action': step['action'], 'status': 'running', 'call_reserved': False}
                    case['execution'].append(record)
                    writer.save(report)
                    action = step['action']
                    if action in ('turn', 'replay'):
                        if action == 'turn':
                            epoch = manager.capture_write_epoch()
                            identifier = store.add_message(session, 'user', step['user'])
                            user = step['user']
                            assistant = step.get('assistant', '这是合成助手的占位回复，不作为证据。')
                            store.add_message(session, 'assistant', assistant)
                            if 'as' in step:
                                references[step['as']] = (identifier, user, assistant)
                        else:
                            identifier, user, assistant = references[step['ref']]
                            epoch = manager.capture_write_epoch()  # Even a current epoch cannot revive an old message.
                        record['saved_user_message'] = store.get_message(identifier)
                        record['write_epoch'] = epoch
                        trace = {}
                        budgeted.record = record
                        saved = await manager.observe_turn(
                            session_id=session, user_text=user, assistant_text=assistant,
                            user_message_id=identifier, write_epoch=epoch,
                            extraction_provider=budgeted, trace=trace, max_attempts=1,
                        )
                        record['extraction'] = trace_result(trace)
                        record['write_rows'] = [row.to_dict() for row in saved]
                        if trace.get('error_type'):
                            failed = True
                            record['status'] = 'error'
                        else:
                            record['status'] = 'complete'
                    elif action == 'message':
                        identifier = store.add_message(session, step['role'], step['text'])
                        record['saved_message'] = store.get_message(identifier)
                        record['status'] = 'complete'
                    elif action == 'forget_all':
                        record['deleted_rows'] = manager.forget_all()
                        record['status'] = 'complete'
                    elif action == 'memory_toggle':
                        manager.enabled = step['enabled']
                        record['enabled'] = manager.enabled
                        record['status'] = 'complete'
                    writer.save(report)
                    if failed:
                        break
                case['retrieval'] = retrieve(manager, case['queries'])
                case['rows_before_reopen'] = manager.snapshot(include_history=True)
            except Exception as exc:
                failed = True
                case['error_type'] = type(exc).__name__
                case['rows_before_reopen'] = manager.snapshot(include_history=True)
            finally:
                store.close()
            reopened = None
            try:
                reopened = runtime.MemoryStore(str(database_path))
                reopened_manager = runtime.MemoryManager(reopened)
                case['rows_after_reopen'] = reopened_manager.snapshot(include_history=True)
                case['reopen_rows_equal'] = case['rows_before_reopen'] == case['rows_after_reopen']
                case['retrieval_after_reopen'] = retrieve(reopened_manager, case['queries'])
            except Exception as exc:
                failed = True
                case['error_type'] = type(exc).__name__
            finally:
                if reopened is not None:
                    reopened.close()
            case['status'] = 'partial' if failed else 'complete'
            writer.save(report)
            if failed:
                report['status'] = 'partial'
                writer.save(report)
                return report
    report['status'] = 'complete'
    writer.save(report)
    return report


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--live', action='store_true')
    result.add_argument('--provider')
    result.add_argument('--base-url')
    result.add_argument('--model')
    result.add_argument('--reasoning-effort', choices=REASONING_EFFORTS, default='')
    result.add_argument('--api-key-env', default='MEMORY_EVAL_API_KEY')
    result.add_argument('--output')
    return result


def main(argv=None):
    args = parser().parse_args(argv)
    writer = None
    report = None
    client = None
    try:
        cases = load_cases()
        config = live_configuration(args) if args.live else None
        mode = 'live' if args.live else 'plan'
        report = make_report(cases, mode=mode, provider=config.llm_provider if config else args.provider,
                             model=config.llm_model if config else args.model,
                             reasoning_effort=config.llm_reasoning_effort if config else args.reasoning_effort)
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')
        output = args.output or ROOT / 'eval' / 'research' / f'memory_eval_{mode}_{stamp}_{uuid4().hex[:8]}.json'
        writer = ReportWriter(output, secret=config.llm_api_key if config else '')
        writer.save(report)
        if args.live:
            runtime = load_runtime()
            client = runtime.LLM(config=config, request_timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False)
            if client.offline:
                raise EvaluationError('live_provider_is_offline')
            async def run():
                try:
                    return await execute_cases(report, writer, client, runtime=runtime)
                finally:
                    await client.aclose()
            asyncio.run(run())
        print(json.dumps({'mode': mode, 'status': report['status'], 'cases': len(cases),
                          'calls_reserved': report['budget']['calls_reserved'], 'output': str(writer.path)}, ensure_ascii=False))
        return 0 if report['status'] in ('planned', 'complete') else 1
    except (Exception, KeyboardInterrupt) as exc:
        code = str(exc) if isinstance(exc, EvaluationError) else type(exc).__name__
        if writer and report:
            report['status'] = 'partial'
            report['error_type'] = type(exc).__name__
            try:
                writer.save(report)
            except Exception:
                pass
        print(json.dumps({'ok': False, 'error': code, 'output': str(writer.path) if writer else None}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
