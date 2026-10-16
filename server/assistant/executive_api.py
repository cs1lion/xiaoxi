"""V3 内阁总管 REST 接缝：督办台账、判例库、API 战况，以及本地透明代理。

所有写路由都带同源校验（与既有 assistant 路由同一套规矩）：单用户本地系统，
不接受跨源写。读失败一律降级为空清单，不让抽屉把主页面带崩。
"""
from __future__ import annotations

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def create_executive_router(executive):
    router = APIRouter(prefix='/api/executive', tags=['executive'])

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

    @router.post('/commissions/{commission_id}/accept')
    async def accept(commission_id: str, request: Request):
        await payload(request)
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
