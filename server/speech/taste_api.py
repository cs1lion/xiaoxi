"""Local, same-origin, explicit TTS audition. No model chat or persistence."""
import asyncio
import base64
from urllib.parse import urlsplit
from fastapi import APIRouter, Request, HTTPException
from .taste import catalog, profile


def create_taste_router(engine):
    router = APIRouter(prefix='/api/voice-taste')
    gate = asyncio.Lock()

    @router.get('')
    async def info():
        return catalog(engine)

    @router.post('')
    async def audition(request: Request):
        origin = request.headers.get('origin')
        if not origin or urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'same_origin_required')
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict) or set(data) != {'text', 'style'}:
            raise HTTPException(400, 'text_and_style_required')
        text, style = data['text'], data['style']
        if not isinstance(text, str) or not text.strip() or len(text) > 240:
            raise HTTPException(400, 'text_must_be_1_to_240_characters')
        if not isinstance(style, str):
            raise HTTPException(400, 'invalid_style')
        try:
            selected = profile(engine, style)
        except ValueError:
            raise HTTPException(400, 'unknown_style')
        if gate.locked():
            raise HTTPException(429, 'audition_busy')
        async with gate:
            try:
                audio = await asyncio.wait_for(engine.synthesize_profile(text, selected), timeout=35)
            except asyncio.TimeoutError:
                raise HTTPException(504, 'audition_timeout')
            except Exception:
                # Do not expose provider errors, endpoints or credentials.
                raise HTTPException(502, 'audition_failed')
        from ..config import settings
        info = catalog(engine)
        # Report the container the bytes actually are. Trimming re-encodes to WAV,
        # so claiming tts_format here would hand the browser a mismatched MIME.
        wire = 'wav' if settings.tts_trim_silence and info['engine'] == 'stepaudio' else settings.tts_format
        return {**info, 'style':style, 'text':text, 'profile':selected,
                'audio':base64.b64encode(audio).decode(),
                'format':wire if info['engine'] == 'stepaudio' else 'mp3'}
    return router
