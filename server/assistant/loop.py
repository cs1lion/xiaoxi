"""Bounded decide/act/observe loop with checkpointed human approval.

A model never grants permissions. Mutations require a single-use confirmation
bound to the exact validated action. Checkpoints store public decisions/results,
not chain-of-thought. Interrupted mutations are not automatically replayed.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from .runtime import RuntimeStore


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    parameters: dict[str, type]
    required: tuple[str, ...]
    handler: Callable[..., Any]
    mutation: bool = False

    def validate(self, arguments: Any) -> dict[str, Any]:
        if not isinstance(arguments, dict) or set(arguments) - set(self.parameters):
            raise ValueError('invalid_tool_arguments')
        if any(key not in arguments for key in self.required):
            raise ValueError('missing_tool_arguments')
        for key, value in arguments.items():
            expected = self.parameters[key]
            if expected is float:
                valid = type(value) in (int, float)
            else:
                valid = type(value) is expected
            if not valid:
                raise ValueError(f'invalid_argument_type:{key}')
            if isinstance(value, str) and len(value) > 12000:
                raise ValueError('argument_too_large')
        from .context import _CREDENTIAL
        if _CREDENTIAL.search(json.dumps(arguments, ensure_ascii=False, allow_nan=False)):
            raise ValueError('sensitive_tool_arguments')
        return arguments

    def spec(self) -> dict[str, Any]:
        types = {str: 'string', float: 'number', int: 'integer', dict: 'object', bool: 'boolean'}
        return {'name': self.name, 'description': self.description, 'mutation': self.mutation,
                'parameters': {'type': 'object', 'properties': {k: {'type': types[t]} for k, t in self.parameters.items()},
                               'required': list(self.required), 'additionalProperties': False}}


class AgentLoop:
    def __init__(self, model: Callable[[dict[str, Any]], Awaitable[dict[str, Any]]],
                 tools: list[Tool], runtime: RuntimeStore, *, max_steps=4,
                 max_tools=3, timeout=30.0, clock=time.time,
                 input_ttl=900.0, max_input_rounds=8):
        self.model = model
        self.tools = {tool.name: tool for tool in tools}
        self.runtime = runtime
        self.max_steps = max_steps
        self.max_tools = max_tools
        self.timeout = timeout
        self.clock = clock
        self.input_ttl = input_ttl
        self.max_input_rounds = max_input_rounds

    @staticmethod
    def action_hash(action: dict[str, Any]) -> str:
        return hashlib.sha256(json.dumps(action, sort_keys=True, ensure_ascii=False,
                                         separators=(',', ':'), allow_nan=False).encode()).hexdigest()

    async def start(self, user_text: str, context: dict[str, Any] | None = None) -> dict[str, Any]:
        from .context import _text
        safe_text = _text(user_text, 12000)
        state = {'user_text': safe_text, 'context': context or {}, 'observations': [],
                 'steps': 0, 'step_limit': self.max_steps, 'tool_count': 0,
                 'pending_action': None, 'input_rounds': 0,
                 'latest_user_text': safe_text,
                 'input_history': [{'role': 'user', 'content': safe_text}],
                 'completed_actions': {}}
        run_id = self.runtime.create(state)
        return await self._guarded(run_id, state)

    @staticmethod
    def cancels_input(text: str) -> bool:
        """Only standalone cancellation; quoted text and task edits remain model input."""
        return bool(re.fullmatch(
            r'\s*(?:取消|取消这个|取消这件事|取消这次操作|取消当前操作|算了|先不做了|不做了|不用了|停止|cancel)[。.!！\s]*',
            text, re.I))

    async def continue_input(self, run_id: str, text: str, *, session_id: str,
                             context: dict[str, Any] | None = None) -> dict[str, Any]:
        from .context import _text
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise ValueError('invalid_input')
        safe_text = _text(text, 12000)
        cancel = self.cancels_input(text)
        state = self.runtime.claim_input(run_id, session_id, safe_text, context=context,
                                        cancel=cancel, max_rounds=self.max_input_rounds,
                                        max_steps=self.max_steps)
        if cancel:
            return self._result(run_id, 'cancelled',
                                '好，这次后续处理已取消。已有执行结果保留在下方。', state)
        return await self._guarded(run_id, state)

    async def resume(self, run_id: str, confirmation_id: str, approve: bool, *, context: dict[str, Any] | None = None) -> dict[str, Any]:
        run = self.runtime.get(run_id)
        if run['status'] != 'waiting_confirmation' or not run['state'].get('pending_action'):
            raise ValueError('run_not_waiting')
        state = run['state']
        if context is not None:
            state['context'] = context
        action = state['pending_action']
        if state.get('confirmation_id') != confirmation_id:
            raise ValueError('confirmation_run_mismatch')
        self.runtime.consume(confirmation_id, self.action_hash(action), approve)
        state['pending_action'] = None
        if not approve:
            self.runtime.save(run_id, 'cancelled', state)
            return self._result(run_id, 'cancelled', '已取消，没有执行这个操作。', state)
        return await self._guarded(run_id, state, approved_action=action)

    async def _guarded(self, run_id, state, approved_action=None):
        try:
            async with asyncio.timeout(self.timeout):
                if approved_action is not None:
                    await self._execute(run_id, state, approved_action)
                return await self._advance(run_id, state)
        except asyncio.CancelledError:
            self.runtime.save(run_id, 'interrupted', state)
            raise
        except TimeoutError:
            self.runtime.save(run_id, 'timed_out', state)
            return self._result(run_id, 'timed_out', '处理超时了。请查看下方已有执行结果，不会自动重试。', state)
        except Exception as exc:
            self.runtime.event(run_id, 'error', {'code': type(exc).__name__})
            self.runtime.save(run_id, 'failed', state)
            return self._result(run_id, 'failed', '这次没能继续处理，不会把它当成完成。', state)

    @staticmethod
    def _raise_if_cancelling():
        # A provider/handler may catch CancelledError and return anyway. Its return
        # value must not allow this cancelled run to plan or approve further work.
        task = asyncio.current_task()
        if task is not None and task.cancelling():
            raise asyncio.CancelledError

    async def _advance(self, run_id, state):
        while state['steps'] < state.get('step_limit', self.max_steps):
            self._raise_if_cancelling()
            state['steps'] += 1
            self.runtime.save(run_id, 'running', state)
            decision = await self.model({'user_text': state['user_text'], 'context': state['context'],
                                         'latest_user_text': state.get('latest_user_text', state['user_text']),
                                         'input_history': state.get('input_history', []),
                                         'observations': state['observations'],
                                         'tools': [t.spec() for t in self.tools.values()],
                                         'remaining_tools': self.max_tools - state['tool_count']})
            self._raise_if_cancelling()
            if not isinstance(decision, dict):
                raise ValueError('invalid_decision')
            action = decision.get('action')
            if action in ('final', 'clarify'):
                text = decision.get('text')
                if not isinstance(text, str) or not text.strip() or len(text) > 12000:
                    raise ValueError('invalid_final')
                if action == 'clarify':
                    state['last_clarification'] = text
                    state.setdefault('input_history', []).append({'role': 'assistant', 'content': text})
                    expires_at = self.runtime.wait_for_input(run_id, state, self.input_ttl)
                    self.runtime.event(run_id, 'input_required', {'text': text, 'expires_at': expires_at})
                    return {**self._result(run_id, 'waiting_input', text, state),
                            'input_expires_at': expires_at}
                self.runtime.save(run_id, 'completed', state)
                return self._result(run_id, 'completed', text, state)
            if action != 'tool_call' or set(decision) - {'action', 'tool', 'arguments'}:
                raise ValueError('invalid_action')
            if state['tool_count'] >= self.max_tools:
                break
            tool = self.tools.get(decision.get('tool'))
            if tool is None:
                state['observations'].append({'status': 'failed', 'error_code': 'unknown_tool'})
                continue
            try:
                tool.validate(decision.get('arguments'))
                self.action_hash(decision)
            except (ValueError, TypeError):
                state['observations'].append({'tool': tool.name, 'status': 'failed', 'error_code': 'invalid_arguments'})
                continue
            if tool.mutation and self.action_hash(decision) in state.get('completed_actions', {}):
                state['observations'].append({'tool': tool.name, 'status': 'skipped',
                    'error_code': 'already_executed',
                    'receipt_index': state['completed_actions'][self.action_hash(decision)]})
                continue
            if tool.mutation:
                state['pending_action'] = decision
                confirmation = self.runtime.confirmation(run_id, self.action_hash(decision))
                state['confirmation_id'] = confirmation['id']
                self.runtime.save(run_id, 'waiting_confirmation', state)
                self.runtime.event(run_id, 'confirmation_required', {'tool': tool.name, 'id': confirmation['id']})
                return {**self._result(run_id, 'waiting_confirmation', '先确认一下这个操作的内容。', state),
                        'confirmation': {**confirmation, 'tool': tool.name, 'arguments': decision['arguments']}}
            await self._execute(run_id, state, decision)
        self.runtime.save(run_id, 'limit_reached', state)
        return self._result(run_id, 'limit_reached', '已到本轮处理上限，先停在这里。已有结果见下方。', state)

    async def _execute(self, run_id, state, action):
        self._raise_if_cancelling()
        tool = self.tools[action['tool']]
        arguments = tool.validate(action['arguments'])
        state['tool_count'] += 1
        self.runtime.event(run_id, 'tool_started', {'tool': tool.name})
        # This checkpoint deliberately precedes side effects; a crash is not replayed.
        self.runtime.save(run_id, 'running', state)
        try:
            value = tool.handler(**arguments)
            if hasattr(value, '__await__'):
                value = await value
            receipt = {'tool': tool.name, 'status': 'succeeded', 'data': value}
        except (ValueError, KeyError) as exc:
            receipt = {'tool': tool.name, 'status': 'failed', 'error_code': str(exc)[:160]}
        state['observations'].append(receipt)
        if tool.mutation and receipt['status'] == 'succeeded':
            state.setdefault('completed_actions', {})[self.action_hash(action)] = len(state['observations']) - 1
        self.runtime.event(run_id, 'tool_finished', receipt)
        self.runtime.save(run_id, 'running', state)
        # Preserve a handler's successful side effect and receipt before stopping.
        self._raise_if_cancelling()

    @staticmethod
    def _result(run_id, status, text, state):
        return {'run_id': run_id, 'status': status, 'text': text,
                'receipts': state['observations'], 'steps': state['steps']}
