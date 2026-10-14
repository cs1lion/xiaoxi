"""Same-origin draft observations and explicitly requested cached-point speech."""
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .editor_insights import InsightConflict, InsightDiscussionError, InsightSpeechError


def create_editor_insights_router(service, engine):
    router = APIRouter(prefix='/api/editor-insights', tags=['editor-insights'])

    def deny(code, detail):
        raise HTTPException(code, detail, headers={'Cache-Control': 'no-store'})

    def same_origin(request, *, required=True):
        origin = request.headers.get('origin')
        if not origin and not required:
            return
        try:
            parsed = urlsplit(origin or '')
            allowed = parsed.netloc == request.headers.get('host') and parsed.scheme == request.url.scheme
        except ValueError:
            allowed = False
        if not allowed:
            deny(403, 'same_origin_required')

    async def data(request, fields, limit=130000):
        same_origin(request)
        if len(await request.body()) > limit:
            deny(413, 'draft_request_too_large')
        try:
            result = await request.json()
        except Exception:
            deny(400, 'invalid_json')
        if not isinstance(result, dict) or set(result) != set(fields):
            deny(400, 'required_fields_only')
        return result

    def result(value):
        return JSONResponse(value, headers={'Cache-Control': 'no-store'})

    def error(exc):
        if isinstance(exc, InsightConflict):
            reason = ('insight_discussion_superseded' if str(exc) == 'insight_discussion_superseded'
                      else 'insight_draft_changed')
            deny(409, reason)
        if isinstance(exc, KeyError):
            deny(404, 'insight_expired_or_revoked')
        if isinstance(exc, (InsightSpeechError, InsightDiscussionError)):
            deny(exc.status_code, exc.reason)
        if isinstance(exc, (TypeError, ValueError)):
            deny(400, str(exc))
        deny(502, 'editor_insight_unavailable')

    @router.get('/status')
    async def status(request: Request):
        same_origin(request, required=False)
        return result(service.status())

    @router.post('/state')
    async def state(request: Request):
        payload = await data(request, ('session_id', 'draft_revision', 'enabled'), limit=2000)
        try:
            return result(service.state(**payload))
        except Exception as exc:
            error(exc)

    @router.post('/analyze')
    async def analyze(request: Request):
        payload = await data(request, ('session_id', 'draft_revision', 'title', 'body', 'enabled'))
        try:
            return result(await service.analyze(**payload))
        except Exception as exc:
            error(exc)

    @router.post('/{insight_id}/speak')
    async def speak(insight_id: str, request: Request):
        payload = await data(request, ('session_id', 'draft_revision'), limit=2000)
        # Follow the configured provider format while the non-Step engines
        # produce mp3. Tests inject a fake engine without bootstrapping config.
        audio_format = 'mp3'
        if getattr(engine, 'engine', None) == 'stepaudio':
            from ..config import settings
            audio_format = settings.tts_format
        try:
            return result(await service.speak(insight_id, engine=engine, audio_format=audio_format, **payload))
        except Exception as exc:
            error(exc)

    @router.post('/{insight_id}/dismiss')
    async def dismiss(insight_id: str, request: Request):
        payload = await data(request, ('session_id', 'draft_revision'), limit=2000)
        try:
            return result(service.dismiss(insight_id, **payload))
        except Exception as exc:
            error(exc)

    @router.post('/{insight_id}/discuss')
    async def discuss(insight_id: str, request: Request):
        payload = await data(request, ('session_id', 'draft_revision', 'text'), limit=8000)
        try:
            return result(await service.reply(insight_id, **payload))
        except Exception as exc:
            error(exc)

    @router.post('/{insight_id}/feedback')
    async def feedback(insight_id: str, request: Request):
        payload = await data(request, ('session_id', 'draft_revision', 'value'), limit=2000)
        try:
            return result(service.feedback(insight_id, **payload))
        except Exception as exc:
            error(exc)

    return router
