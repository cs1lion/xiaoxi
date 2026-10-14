"""Explicit local-user companion thread routes; client cannot forge receipts."""
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse


def create_threads_router(service):
    router = APIRouter(prefix='/api/threads', tags=['companion'])

    def invoke(fn, *args, **kwargs):
        from .threads import RevisionConflict
        try:
            return JSONResponse(fn(*args, **kwargs), headers={'Cache-Control': 'no-store'})
        except RevisionConflict:
            raise HTTPException(409, 'revision_conflict')
        except KeyError:
            raise HTTPException(404, 'thread_or_event_not_found')
        except (ValueError, TypeError) as exc:
            raise HTTPException(400, str(exc))

    async def body(request, fields):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross_origin_write_denied')
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict) or set(data) - set(fields):
            raise HTTPException(400, 'unknown_fields')
        return data

    @router.get('')
    def listing():
        return invoke(lambda: {'items': service.threads.list_threads()})

    @router.post('')
    async def create(request: Request):
        return invoke(service.threads.create, **await body(request, ['title']))

    @router.get('/selection')
    def selection():
        service.participation.current()
        return invoke(lambda: {'thread_id': service.participation.selected_id})

    @router.post('/selection')
    async def select(request: Request):
        data = await body(request, ['thread_id'])
        if 'thread_id' not in data:
            raise HTTPException(400, 'thread_id_required')
        return invoke(service.participation.select, data['thread_id'])

    @router.get('/{thread_id}/proposals')
    def proposals(thread_id: str):
        return invoke(lambda: {'items': service.proposals.list_pending(thread_id)})

    @router.get('/{thread_id}/materials')
    def materials(thread_id: str):
        return invoke(service.list_thread_materials, thread_id)

    @router.post('/{thread_id}/materials')
    async def link_material(thread_id: str, request: Request):
        return invoke(service.link_thread_material, thread_id, **await body(request,
                      ['expected_revision', 'note_id', 'version', 'purpose']))

    @router.patch('/{thread_id}/materials/{material_id}')
    async def update_material(thread_id: str, material_id: str, request: Request):
        return invoke(service.update_thread_material, thread_id, material_id,
                      **await body(request, ['expected_revision', 'version', 'purpose']))

    @router.post('/{thread_id}/materials/{material_id}/remove')
    async def remove_material(thread_id: str, material_id: str, request: Request):
        return invoke(service.remove_thread_material, thread_id, material_id,
                      **await body(request, ['expected_revision']))

    @router.post('/{thread_id}/proposals')
    async def propose(thread_id: str, request: Request):
        return invoke(service.proposals.propose, thread_id,
                      **await body(request, ['utterance']))

    @router.get('/{thread_id}')
    def get(thread_id: str):
        return invoke(service.threads.get, thread_id)

    @router.patch('/{thread_id}')
    async def update(thread_id: str, request: Request):
        return invoke(service.threads.update, thread_id, **await body(request,
            ['expected_revision', 'title', 'status', 'next_step', 'allow_callbacks']))

    @router.post('/{thread_id}/events')
    async def event(thread_id: str, request: Request):
        data = await body(request, ['expected_revision', 'kind', 'text'])
        if data.get('kind') not in ('decision', 'question', 'progress'):
            raise HTTPException(400, 'user_event_kind_required')
        return invoke(service.threads.add_event, thread_id, source='user_stated', **data)

    @router.patch('/{thread_id}/events/{event_id}')
    async def revise(thread_id: str, event_id: str, request: Request):
        return invoke(service.threads.revise_event, thread_id, event_id,
                      **await body(request, ['expected_revision', 'text']))

    @router.get('/{thread_id}/events/{event_id}/history')
    def history(thread_id: str, event_id: str):
        return invoke(lambda: {'items': service.threads.event_history(thread_id, event_id)})

    @router.post('/{thread_id}/delete')
    async def delete(thread_id: str, request: Request):
        response = invoke(service.threads.delete, thread_id,
                          **await body(request, ['expected_revision']))
        service.proposals.discard_thread(thread_id)
        if service.participation.selected_id == thread_id:
            service.participation.select(None)
        return response

    return router
