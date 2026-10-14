"""Small live-model counterfactual probe, synthetic data only, no action tools.

Use --dry for deterministic prompt checks; default calls configured provider.
Outputs public responses/usage, never credentials or model hidden reasoning.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
import time

os.environ['DB_PATH'] = ':memory:'
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from server.config import settings
settings.db_path = ':memory:'
from server.interaction.preferences import PreferenceStore
from server.interaction.broker import ContextBroker
from server.persona import build_messages
from server.llm import llm

CASES = [
    {'id': 'detail_short', 'text': 'Python 的列表和元组有什么区别？', 'dimension': 'detail', 'value': 'short'},
    {'id': 'detail_long', 'text': 'Python 的列表和元组有什么区别？', 'dimension': 'detail', 'value': 'detailed'},
    {'id': 'current_override', 'text': 'Python 的列表和元组有什么区别？请详细解释并举例。', 'dimension': 'detail', 'value': 'short'},
    {'id': 'scope_miss', 'text': '为什么下雨后空气闻起来不一样？', 'dimension': 'detail', 'value': 'detailed', 'scope': {'activity': 'technical'}},
    {'id': 'listen', 'text': '今天忙了一天，感觉什么都没做成。', 'dimension': 'support_style', 'value': 'listen'},
    {'id': 'help', 'text': '今天忙了一天，感觉什么都没做成。', 'dimension': 'support_style', 'value': 'help'},
    {'id': 'mixed', 'text': '烦死了，帮我写一句简短的周报开头。', 'dimension': 'support_style', 'value': 'listen'},
    {'id': 'no_history', 'text': '我们上次用的那个写作方法叫什么？'},
]

async def run(output, dry):
    results = []
    for case in CASES:
        store = PreferenceStore()
        try:
            if 'dimension' in case:
                e = store.add_evidence('虚构测试用户明确选择该偏好，仅用于隔离评测')
                store.create_claim(case['dimension'], case['value'], [e['id']], scope=case.get('scope'))
            broker = ContextBroker(store)
            bundle = broker.build(case['text'], case['id'])
            messages = build_messages(user_text=case['text'], memory_found=False,
                                      response_strategy=broker.render(bundle))
            row = {'id': case['id'], 'input': case['text'], 'policy': bundle['response_policy'],
                   'prompt_chars': sum(len(m['content']) for m in messages)}
            if not dry:
                if llm.offline:
                    raise RuntimeError('live_model_not_configured')
                started = time.perf_counter()
                try:
                    async with asyncio.timeout(65):
                        reply = await llm.complete_raw(messages, temperature=0.2, max_tokens=1400)
                    row.update(response=reply['text'], finish_reason=reply['finish_reason'], usage=reply['usage'],
                               latency_ms=round((time.perf_counter()-started)*1000))
                    row['surface_checks'] = {
                        'nonempty': bool(reply['text'].strip()),
                        'no_profile_narration': not any(x in reply['text'] for x in ('根据你的偏好', '根据你的档案', '根据你的设置')),
                        'not_truncated': reply['finish_reason'] != 'length',
                    }
                except Exception as exc:
                    row['error_type'] = type(exc).__name__
            results.append(row)
            output.write_text(json.dumps({'model': llm.model, 'dry': dry, 'cases': results,
                'note': 'Surface checks are not semantic grades; human review required.'}, ensure_ascii=False, indent=2), encoding='utf-8')
            print(case['id'], row.get('error_type', 'dry' if dry else row.get('finish_reason')), flush=True)
        finally:
            store.close()
    await llm.aclose()
    return 1 if any('error_type' in row for row in results) else 0

if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry', action='store_true')
    parser.add_argument('--output', default='eval/personalization_live.json')
    args = parser.parse_args()
    target = ROOT / args.output
    if target.exists():
        parser.error('output already exists; choose a new --output to preserve prior evidence')
    target.parent.mkdir(parents=True, exist_ok=True)
    raise SystemExit(asyncio.run(run(target, args.dry)))
