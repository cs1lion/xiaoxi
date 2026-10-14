"""Same-origin review actions; only confirmation writes a saved note version."""

from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from .note_proposals import NoteProposalConflict


def create_note_proposals_router(service):
    router = APIRouter(prefix='/api', tags=['notes'])

    def same_origin(request):
        origin = request.headers.get('origin')
        if origin:
            try:
                parsed = urlsplit(origin)
            except ValueError:
                raise HTTPException(403, 'cross_origin_request_denied')
            if parsed.netloc != request.headers.get('host') or parsed.scheme != request.url.scheme:
                raise HTTPException(403, 'cross_origin_request_denied')

    async def data(request, fields):
        same_origin(request)
        try:
            value = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(value, dict) or set(value) - set(fields):
            raise HTTPException(400, 'unknown_fields')
        return value

    def call(fn, *args, **kwargs):
        try:
            return JSONResponse(fn(*args, **kwargs), headers={'Cache-Control': 'no-store'})
        except NoteProposalConflict as exc:
            raise HTTPException(409, 'draft_source_changed' if str(exc) == 'note source changed'
                                else 'proposal_or_note_version_changed')
        except KeyError:
            raise HTTPException(404, 'proposal_or_note_expired_deleted_or_consumed')
        except (TypeError, ValueError) as exc:
            raise HTTPException(400, str(exc))

    @router.get('/notes/{note_id}/proposals')
    async def pending(note_id: str, request: Request):
        same_origin(request)
        return call(lambda: {'items': service.note_proposals.list_pending(note_id)})

    @router.get('/note-proposals/{proposal_id}')
    async def get(proposal_id: str, request: Request):
        same_origin(request)
        return call(service.note_proposals.get, proposal_id)

    @router.patch('/note-proposals/{proposal_id}')
    async def revise(proposal_id: str, request: Request):
        return call(service.note_proposals.revise, proposal_id,
                    **await data(request, ('expected_version', 'title', 'body')))

    @router.post('/note-proposals/{proposal_id}/confirm')
    async def confirm(proposal_id: str, request: Request):
        return call(service.note_proposals.confirm, proposal_id,
                    **await data(request, ('expected_version',)))

    @router.post('/note-proposals/{proposal_id}/reject')
    async def reject(proposal_id: str, request: Request):
        return call(service.note_proposals.reject, proposal_id,
                    **await data(request, ('expected_version',)))

    return router
