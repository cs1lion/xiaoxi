"""V3 内阁总管 REST 接缝：督办台账、判例库、API 战况，以及本地透明代理。

所有写路由都带同源校验（与既有 assistant 路由同一套规矩）：单用户本地系统，
不接受跨源写。读失败一律降级为空清单，不让抽屉把主页面带崩。
"""
from __future__ import annotations

import asyncio
import time
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .durable_leases import (
    ConfirmationError, ProposalError, describe_confirmation, describe_proposal,
)
from .durable_review import build_review
from .durable_state import (
    DurableApprovals, DurableState, audit, finalize_accept, finalize_reject,
)


def create_executive_router(executive, durable_state: DurableState | None = None):
    router = APIRouter(prefix='/api/executive', tags=['executive'])
    # The workbench and the sidecar-facing router share one DurableState in
    # app.py. A standalone executive router (older tests) simply has no
    # orchestrator authority surface, so the /durable/* routes return 503.
    # A workbench poll must not wait 10s per dead sidecar; it degrades to an
    # empty list instead.
    approvals = DurableApprovals(executive, timeout=2.0) if durable_state is not None else None

    async def payload(request: Request, fields=None):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross_origin_write_denied')
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict):
            raise HTTPException(400, 'object_required')
        if fields is not None and set(data) - set(fields):
            raise HTTPException(400, 'unknown_fields')
        return data

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, 'commission_not_found')
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))

    async def acall(fn, *args, **kwargs):
        """同一个错误映射的异步版：服务端自动驱动执行体的入口走这里。"""
        try:
            return await fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, 'commission_not_found')
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))

    def ready(value):
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

    @router.get('/commissions')
    async def commissions(status: str | None = None, limit: int = 50):
        return ready({'items': call(executive.commissions, status, limit)})

    @router.post('/commissions')
    async def open_commission(request: Request):
        data = await payload(request, ['title', 'brief', 'thread_id'])
        title = data.get('title')
        if not isinstance(title, str) or not title.strip():
            raise HTTPException(400, 'title_required')
        return ready(call(executive.open, title.strip()[:200], str(data.get('brief') or '')[:4000],
                          str(data.get('thread_id') or '')[:100]))

    @router.get('/commissions/{commission_id}')
    async def commission(commission_id: str):
        return ready(call(executive.commission, commission_id))

    @router.get('/commissions/{commission_id}/events')
    async def commission_events(commission_id: str, limit: int = 50):
        return ready({'items': call(executive.commission_events, commission_id, limit)})

    @router.post('/commissions/{commission_id}/answer')
    async def answer(commission_id: str, request: Request):
        data = await payload(request, ['answer'])
        # answer 会驱动执行体接着跑（服务端唯一的执行所有权），因此是协程。
        return ready(await acall(executive.answer, commission_id, str(data.get('answer') or '')))

    @router.get('/commissions/{commission_id}/review')
    async def review(commission_id: str):
        """Read-only C3 review for the workbench; no ledger mutation."""
        commission = call(executive.commission, commission_id)
        if str(commission.get('status')) != 'ready_to_report':
            raise HTTPException(409, 'commission_not_ready_to_report')
        return ready(build_review(executive.evaluator, commission))

    @router.post('/commissions/{commission_id}/accept')
    async def accept(commission_id: str, request: Request):
        await payload(request)
        # The workbench's accept button is the boss's authorization. It must
        # still pass the same C3 review the orchestrator sees: artifact recheck,
        # blockers, L1/L2.
        commission = call(executive.commission, commission_id)
        review = build_review(executive.evaluator, commission)
        if not review['can_accept']:
            raise HTTPException(409, {
                'code': 'accept_not_allowed',
                'blockers': review['blockers'],
                'artifact_recheck_failed': review['artifact_recheck_failed'],
                'recommendation': review['recommendation'],
            })
        return ready(call(executive.accept, commission_id))

    @router.post('/commissions/{commission_id}/reject')
    async def reject(commission_id: str, request: Request):
        data = await payload(request, ['feedback'])
        feedback = str(data.get('feedback') or '')
        if len(feedback.strip()) < 4:
            raise HTTPException(400, 'feedback_too_short_to_learn_from')
        return ready(call(executive.reject, commission_id, feedback[:2000]))

    @router.post('/commissions/{commission_id}/cancel')
    async def cancel(commission_id: str, request: Request):
        await payload(request)
        return ready(call(executive.cancel, commission_id))

    @router.post('/commissions/{commission_id}/submit')
    async def submit(commission_id: str, request: Request):
        """Worker 交差入口：外部执行体（CLI / HTTP / MCP）跑完从这里把成果交回来。

        没有这条路由，适配器只能被进程内驱动；外部 Worker 就只能在stdout里
        自说自话。交上来的 artifact 直接进初审室，和内部链路同一套判据。
        """
        data = await payload(request, ['artifact'])
        artifact = data.get('artifact')
        if not isinstance(artifact, dict):
            raise HTTPException(400, 'artifact_object_required')
        return ready(call(executive.submit, commission_id, artifact))

    @router.post('/commissions/{commission_id}/run')
    async def run(commission_id: str, request: Request):
        """进程内驱动执行体跑完这一单：适配器事件流 -> 遥测 -> 初审。"""
        await payload(request)
        try:
            return ready(await executive.run_commission(commission_id))
        except ValueError as exc:
            raise HTTPException(400, str(exc))

    @router.get('/adapters')
    async def adapters():
        """已登记的适配器类型，以及每个 Agent 当前绑的是哪一个。"""
        return ready({'types': call(executive.adapter_types),
                      'agents': call(executive.adapter_bindings)})

    @router.get('/roster')
    async def roster():
        """花名册只读投影：id / name / strengths / adapter（D4，供像素世界做能力映射）。

        新增端点：/adapters 的既有返回结构一行不动，旧客户端继续读它；新客户端
        优先读这里，读不到时回退 /adapters。只读配置，不实例化适配器、不写库。
        """
        from .dispatcher import roster_agents

        items = []
        for entry in roster_agents(executive.roster):
            if not isinstance(entry, dict) or not entry.get('id'):
                continue
            raw_strengths = entry.get('strengths')
            strengths = ([str(word) for word in raw_strengths if isinstance(word, str)]
                         if isinstance(raw_strengths, list) else [])
            items.append({'id': str(entry['id']),
                          'name': str(entry.get('name') or ''),
                          'strengths': strengths,
                          'adapter': str(entry.get('adapter') or '')})
        return ready({'items': items})

    @router.get('/telemetry')
    async def telemetry(window_seconds: int = 86400):
        if type(window_seconds) is not int or not 60 <= window_seconds <= 30 * 86400:
            raise HTTPException(400, 'invalid_window_seconds')
        return ready(call(executive.telemetry_summary, window_seconds=window_seconds))

    @router.get('/telemetry/report')
    async def telemetry_report(window_seconds: int = 86400):
        if type(window_seconds) is not int or not 60 <= window_seconds <= 30 * 86400:
            raise HTTPException(400, 'invalid_window_seconds')
        return ready({'text': call(executive.telemetry_report, window_seconds=window_seconds)})

    @router.get('/rubrics')
    async def rubrics(active_only: bool = True):
        return ready({'items': call(executive.rubrics, active_only)})

    @router.post('/rubrics/{rubric_id}/delete')
    async def delete_rubric(rubric_id: str, request: Request):
        await payload(request)
        return ready({'deleted': call(executive.deactivate_rubric, rubric_id)})

    # ---------- C4 workbench authority surface ----------
    # The browser is the boss's hand on this machine. Every write below is
    # same-origin checked and only relays an explicit click; the model never
    # reaches these routes, and none of them lets a model commit on its own.

    def need_state():
        if durable_state is None:
            raise HTTPException(503, 'durable_not_configured')
        return durable_state

    def approval_error(exc: Exception):
        code = str(exc.args[0]) if exc.args else ''
        if isinstance(exc, LookupError):
            if code == 'commission_not_found':
                raise HTTPException(404, 'commission_not_found')
            raise HTTPException(409, 'commission_not_durable')
        if isinstance(exc, ConnectionError):
            raise HTTPException(503, 'durable_endpoint_unavailable')
        raise HTTPException(502, 'durable_approval_failed')

    @router.get('/durable/confirmations')
    async def durable_confirmations():
        state = need_state()
        items = []
        for item in state.confirmations.list_pending_all():
            described = describe_confirmation(item)
            items.append({
                'confirmation_id': described['confirmation_id'],
                'agent_id': described['agent_id'],
                'title': described['title'],
                'brief': described['brief'],
                'created_at': item.created_at,
                'expires_at': described['expires_at'],
            })
        return ready({'items': items})

    @router.post('/durable/confirmations/{confirmation_id}')
    async def decide_confirmation(confirmation_id: str, request: Request):
        state = need_state()
        data = await payload(request, ['approved'])
        if not isinstance(data.get('approved'), bool):
            raise HTTPException(400, 'approved_must_be_boolean')
        try:
            item = state.confirmations.decide(confirmation_id, data['approved'])
        except ConfirmationError as exc:
            raise HTTPException(exc.status, exc.code)
        audit(executive, f'confirmation:{confirmation_id}', 'confirm',
              200 if item.status == 'approved' else 403)
        return ready({'confirmation_id': item.confirmation_id, 'status': item.status,
                      'title': item.title, 'expires_at': item.expires_at})

    @router.get('/durable/approvals')
    async def durable_approvals():
        need_state()
        active = [item for item in executive.open_commissions()
                  if str(item.get('status') or '') in
                  ('briefing', 'running', 'auditing', 'blocked')]
        # Parallel with per-call exceptions: one unreachable durable commission
        # must not hide the others or stretch the poll to N × timeout.
        results = await asyncio.gather(
            *(approvals.pending(str(item.get('id') or '')) for item in active),
            return_exceptions=True,
        )
        rows = []
        for commission, pending in zip(active, results):
            if isinstance(pending, BaseException) or not isinstance(pending, list):
                continue
            for item in pending:
                rows.append({
                    'commission_id': str(commission.get('id') or ''),
                    'title': str(commission.get('title') or '')[:200],
                    'agent_id': str(commission.get('agent_id') or ''),
                    'status': str(commission.get('status') or ''),
                    'tool': str(item.get('tool') or ''),
                    'action_hash': str(item.get('action_hash') or ''),
                })
        return ready({'items': rows})

    @router.post('/durable/approvals')
    async def decide_approval(request: Request):
        need_state()
        data = await payload(request, ['commission_id', 'tool', 'action_hash', 'approved'])
        commission_id = str(data.get('commission_id') or '')
        tool = str(data.get('tool') or '')
        action_hash = str(data.get('action_hash') or '')
        if not isinstance(data.get('approved'), bool):
            raise HTTPException(400, 'approved_must_be_boolean')
        approved = data['approved']
        if not commission_id or not tool:
            raise HTTPException(400, 'commission_and_tool_required')
        try:
            pending = await approvals.pending(commission_id)
            match = [item for item in pending if item.get('tool') == tool and
                     (not action_hash or item.get('action_hash') == action_hash)]
            if not match:
                raise HTTPException(409, 'approval_not_pending')
            selected = action_hash or str(match[0].get('action_hash') or '')
            remaining = await approvals.decide(commission_id, tool=tool,
                                              action_hash=selected, approved=approved)
        except HTTPException:
            raise
        except Exception as exc:
            approval_error(exc)
        resumed = False
        if approved:
            commission = call(executive.commission, commission_id)
            if str(commission.get('status')) == 'blocked':
                try:
                    executive.dispatcher.answer(commission_id, '已放行，继续。')
                except ValueError:
                    pass
                else:
                    await executive.resume(commission_id)
                    resumed = True
        audit(executive, commission_id, 'approve_tool' if approved else 'revoke_tool')
        return ready({'commission_id': commission_id, 'tool': tool,
                      'action_hash': selected, 'approved': approved,
                      'resumed': resumed, 'pending': remaining})

    @router.get('/durable/proposals')
    async def durable_proposals():
        state = need_state()
        rows = []
        for item in state.proposals.list_open():
            try:
                commission = executive.commission(item.commission_id)
            except KeyError:
                continue
            if str(commission.get('status')) != 'ready_to_report':
                continue
            review = build_review(executive.evaluator, commission)
            rows.append({
                **describe_proposal(item),
                'title': str(commission.get('title') or '')[:200],
                'recommendation': review['recommendation'],
                'can_accept': review['can_accept'],
                'blockers': review['blockers'],
                'artifacts_verified': review['artifacts_verified'],
                'artifact_recheck_failed': review['artifact_recheck_failed'],
                'artifact_unverified_count': review['artifact_unverified_count'],
            })
        return ready({'items': rows})

    @router.post('/durable/proposals/{proposal_id}/commit')
    async def commit_proposal(proposal_id: str, request: Request):
        state = need_state()
        data = await payload(request, ['action', 'feedback'])
        action = str(data.get('action') or '')
        feedback = str(data.get('feedback') or '').strip()
        if action not in ('accept', 'reject'):
            raise HTTPException(400, 'action_must_be_accept_or_reject')
        if action == 'reject' and len(feedback) < 4:
            raise HTTPException(400, 'feedback_too_short_to_learn_from')
        previous = state.commit_results.get(proposal_id)
        if previous is not None:
            if previous.get('action') != action:
                raise HTTPException(409, 'proposal_action_mismatch')
            return ready({**previous['result'], 'duplicate': True})
        try:
            item = state.proposals.get(proposal_id)
        except ProposalError as exc:
            raise HTTPException(exc.status, exc.code)
        if item.action != action:
            raise HTTPException(409, 'proposal_action_mismatch')
        try:
            commission = executive.ledger.get(item.commission_id)
        except Exception:
            raise HTTPException(404, 'commission_not_found')
        review = build_review(executive.evaluator, commission)
        if action == 'accept' and not review['can_accept']:
            raise HTTPException(409, {
                'code': 'accept_not_allowed',
                'blockers': review['blockers'],
                'artifact_recheck_failed': review['artifact_recheck_failed'],
                'recommendation': review['recommendation'],
            })
        if action == 'reject' and not review['can_reject']:
            raise HTTPException(409, 'reject_not_allowed')
        try:
            if (item.status == 'authorized' and
                    item.authorization_expires_at > time.time() and item.authorization_id):
                # The boss is clicking again after a transient commit failure;
                # the fresh review above still guards this attempt.
                authorization_id = item.authorization_id
            else:
                authorization_id = state.proposals.authorize(
                    proposal_id, source='boss').authorization_id
        except ProposalError as exc:
            raise HTTPException(exc.status, exc.code)
        audit(executive, item.commission_id, 'authorize', detail='boss')
        try:
            if action == 'accept':
                return ready(finalize_accept(executive, state, proposal_id, authorization_id))
            return ready(finalize_reject(executive, state, proposal_id, authorization_id, feedback))
        except ProposalError as exc:
            raise HTTPException(exc.status, exc.code)

    return router


def create_telemetry_proxy_router(executive):
    """POST /internal/telemetry_proxy：本地透明 API 代理，旁路记账、不改请求体。"""
    # 变量名不能和上面的 router 重名：check_protocol.py 按文件收集 APIRouter 前缀，
    # 同名会把 /api/executive 的前缀覆盖成 /internal，前端引用全部变成静默 404。
    proxy_router = APIRouter(prefix='/internal', tags=['telemetry'])

    @proxy_router.post('/telemetry_proxy')
    async def telemetry_proxy(request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross_origin_write_denied')
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict):
            raise HTTPException(400, 'object_required')
        if not isinstance(data.get('path'), str) or not data['path'].startswith('/'):
            raise HTTPException(400, 'absolute_path_required')
        from .telemetry import http_transport
        from ..config import settings
        transport = http_transport(settings.llm_base_url, api_key=settings.llm_api_key)
        try:
            return await executive.proxy(data, transport)
        except Exception as exc:
            raise HTTPException(502, f'upstream_failed:{type(exc).__name__}')

    return proxy_router


__all__ = ['create_executive_router', 'create_telemetry_proxy_router']
