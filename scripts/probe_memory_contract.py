"""Offline research probes of desired memory contracts, using synthetic data.

These are diagnostic experiments, not an LLM accuracy benchmark. Handwritten
candidate payloads simulate model output; source messages and linked writes use
real isolated in-memory storage. No provider or user database is accessed.
Run: .venv/Scripts/python.exe -B scripts/probe_memory_contract.py
Each result reports whether a proposed contract is currently satisfied. Missing
contracts are recorded as gaps and do not make the diagnostic command fail.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import sys
from unittest.mock import AsyncMock, patch

# Isolate even the application's import-time singleton before config is loaded.
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

with patch('dotenv.load_dotenv', return_value=False):
    from server.interaction.broker import ContextBroker
    from server.interaction.preferences import PreferenceStore
    from server.memory.extractor import parse_extraction
    from server.memory.manager import MemoryManager
    from server.memory.store import MemoryStore


def candidate(key, value, source='stated', *, quote=None, message_id=None, operation='add'):
    result = {'kind': 'fact', 'subject': 'user', 'key': key, 'value': value,
              'source': source, 'importance': .8, 'operation': operation}
    if quote is not None:
        result['evidence'] = {'message_id': message_id, 'quote': quote}
    return result


def brief(memory):
    return {'key': memory.key, 'value': memory.value,
            'source': memory.source, 'status': memory.status,
            'evidence_message_id': memory.evidence_message_id,
            'evidence_quote': memory.evidence_quote,
            'evidence_state': memory.evidence_state, 'decision': memory.decision}


async def linked_turn(database, manager, user_text, items, *, session_id='synthetic'):
    """Save the actual user turn, then run the real linked extraction/write path.

    Only the provider's returned JSON is fake. No manual remember/upsert seeds
    the expected answer; quote/role/ID validation and conflicts execute normally.
    """
    epoch = manager.capture_write_epoch()
    identifier = database.add_message(session_id, 'user', user_text, 'text')
    payload = []
    for item in items:
        item = dict(item)
        if isinstance(item.get('evidence'), dict):
            item['evidence'] = {**item['evidence'], 'message_id': identifier}
        payload.append(item)
    model = AsyncMock(return_value={'text': json.dumps({'memories': payload}, ensure_ascii=False)})
    with patch('server.memory.extractor.llm.complete_raw', model):
        saved = await manager.observe_turn(
            session_id=session_id, user_text=user_text, assistant_text='虚构助手回复不作证据',
            user_message_id=identifier, write_epoch=epoch,
        )
    return {'message': database.get_message(identifier), 'model_stub_calls': model.await_count,
            'saved': [brief(memory) for memory in saved]}


def policy_probe(text, *, value='short', scope=None):
    with_store = PreferenceStore()
    try:
        evidence = with_store.add_evidence('虚构评测用户在设置面板明确选择该偏好')
        with_store.create_claim('detail', value, [evidence['id']], scope=scope)
        bundle = ContextBroker(with_store).build(text, 'synthetic-probe')
        policy = bundle['response_policy']
        return {'input': text, 'activity': bundle['scene']['activity'],
                'length': policy['length'], 'current_overrides': policy['current_overrides'],
                'excluded_reasons': [entry['reason'] for entry in bundle['trace']['excluded']]}
    finally:
        with_store.close()


def run():
    rows = []

    def record(identifier, contract, observed, satisfied):
        rows.append({'id': identifier, 'contract': contract, 'observed': observed,
                     'satisfied': bool(satisfied)})

    database = MemoryStore(':memory:')
    try:
        identifier = database.add_message('synthetic', 'user', '我在甲公司工作，岗位是设计师。')
        message = database.get_message(identifier)
        independent = [candidate('工作:公司', '用户在甲公司工作', quote='我在甲公司工作', message_id=identifier),
                       candidate('工作:岗位', '用户的岗位是设计师', quote='岗位是设计师', message_id=identifier)]
        parsed = parse_extraction({'memories': independent}, evidence_messages=[message])
        record('independent_same_batch', '同主题的公司和岗位是独立事实，应同时保留',
               {'source_message': message, 'parsed': parsed},
               len(parsed) == 2 and {item['key'] for item in parsed} == {'工作:公司', '工作:岗位'}
               and all(item['evidence_state'] == 'linked' for item in parsed))
    finally:
        database.close()

    database = MemoryStore(':memory:')
    try:
        manager = MemoryManager(database)
        turns = [
            asyncio.run(linked_turn(database, manager, '我在甲公司工作。', [
                candidate('工作:公司', '用户在甲公司工作', quote='我在甲公司工作')])),
            asyncio.run(linked_turn(database, manager, '我的岗位是设计师。', [
                candidate('工作:岗位', '用户的岗位是设计师', quote='我的岗位是设计师')])),
        ]
        active = database.list_memories(status='active')
        record('independent_across_turns', '分轮写入独立工作事实，不应使另一条失效',
               {'turns': turns, 'memories': [brief(m) for m in database.list_memories(include_history=True)]},
               len(active) == 2 and {memory.key for memory in active} == {'工作:公司', '工作:岗位'}
               and all(memory.evidence_state == 'linked' for memory in active)
               and all(turn['model_stub_calls'] == 1 and len(turn['saved']) == 1 for turn in turns))
    finally:
        database.close()

    database = MemoryStore(':memory:')
    try:
        manager = MemoryManager(database)
        stated_turn = asyncio.run(linked_turn(database, manager, '我喜欢文明6。', [
            candidate('游戏:兴趣', '用户明确说喜欢文明6', quote='我喜欢文明6')]))
        inferred_turn = asyncio.run(linked_turn(database, manager, '我可能不喜欢文明6。', [
            candidate('游戏:兴趣', '推测用户不喜欢文明6', 'inferred', quote='我可能不喜欢文明6')]))
        active = database.list_memories(status='active')
        record('inference_cannot_replace_stated', '未经确认的推测不能让明确事实失效',
               {'turns': [stated_turn, inferred_turn],
                'memories': [brief(m) for m in database.list_memories(include_history=True)]},
               len(active) == 1 and active[0].source == 'stated'
               and active[0].value == '用户明确说喜欢文明6' and active[0].evidence_state == 'linked'
               and stated_turn['model_stub_calls'] == inferred_turn['model_stub_calls'] == 1
               and len(stated_turn['saved']) == len(inferred_turn['saved']) == 1
               and inferred_turn['saved'][0]['source'] == 'inferred'
               and inferred_turn['saved'][0]['status'] == 'conflict'
               and inferred_turn['saved'][0]['evidence_state'] == 'linked')
    finally:
        database.close()

    unsupported = parse_extraction({'memories': [
        candidate('工作:公司', '用户在未由任何原话支持的公司工作')]}, evidence_messages=[])
    record('message_evidence_required', '事实准入应能拒绝无消息证据的模型候选',
           {'evidence_messages': [], 'parse_mode': 'strict_linked', 'accepted': bool(unsupported),
            'candidate_fields': sorted(unsupported[0]) if unsupported else []},
           not unsupported)

    observed = policy_probe('Python 的列表和元组有什么区别？请详细解释。')
    record('current_override_control', '当前明确要求详细，覆盖历史简短偏好',
           observed, observed['length'] == 'detailed')

    observed = policy_probe('Python 的列表和元组有什么区别？请详细解释并举例。')
    record('current_override_with_example', '增加“并举例”不应取消当前详细要求',
           observed, observed['length'] == 'detailed')

    observed = policy_probe('帮我写个 Python 文件重命名脚本。', value='detailed',
                            scope={'activity': 'technical'})
    record('technical_scope_control', '明确 Python 脚本请求应匹配技术偏好',
           observed, observed['activity'] == 'technical' and observed['length'] == 'detailed')

    observed = policy_probe('帮我写个批量重命名文件的脚本。', value='detailed',
                            scope={'activity': 'technical'})
    record('technical_scope_paraphrase', '省略语言名的脚本请求仍应匹配技术偏好',
           observed, observed['activity'] == 'technical' and observed['length'] == 'detailed')

    return {'date': '2026-10-13', 'mode': 'offline_synthetic_contract_probe',
            'notes': ['无真实模型调用；不能据此估计模型抽取准确率或最终生成质量。',
                      '候选为人工构造；同批解析使用真实隔离存储消息，分轮与来源保护运行真实 observe_turn、解析及写入链路，仅模型输出为stub。',
                      '所有自动候选采用严格消息证据契约，无证据probe明确传空evidence_messages；不把legacy诊断解析当准入。',
                      'DB_PATH 在导入前固定为内存，所有实验数据库为内存数据库。',
                      '导入前固定LLM_PROVIDER=mock、LLM_API_KEY为空，并屏蔽dotenv读取，不加载用户.env。',
                      'satisfied=false 表示拟定契约尚未满足，不代表已有测试运行失败。'],
            'satisfied': sum(row['satisfied'] for row in rows),
            'gaps': sum(not row['satisfied'] for row in rows), 'probes': rows}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='eval/research/memory_contract_probe_evidence_20261013.json')
    args = parser.parse_args()
    target = ROOT / args.output
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        stem, suffix = target.stem, target.suffix
        target = target.with_name(f'{stem}_new{suffix}')
        index = 2
        while target.exists():
            target = target.with_name(f'{stem}_new{index}{suffix}')
            index += 1
    # Exclusive creation preserves prior experimental evidence, including on rerun.
    with target.open('x', encoding='utf-8') as stream:
        result = run()
        json.dump(result, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps({'satisfied': result['satisfied'], 'gaps': result['gaps'],
                      'output': str(target)}, ensure_ascii=False))
