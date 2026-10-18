"""Assistant REST routes, same-origin mutation guard, single-process scheduler."""
from __future__ import annotations

import asyncio
from contextlib import suppress
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request


def create_router(service):
    router = APIRouter(prefix='/api', tags=['assistant'])

    async def payload(request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross_origin_write_denied')
        try:
            data = await request.json()
        except Exception:
            raise HTTPException(400, 'invalid_json')
        if not isinstance(data, dict):
            raise HTTPException(400, 'object_required')
        return data

    def call(fn, *args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError:
            raise HTTPException(404, 'object_not_found')
        except (ValueError, TypeError) as exc:
            if str(exc) == 'selection_conflict':
                raise HTTPException(409, 'selection_conflict')
            raise HTTPException(400, str(exc))

    @router.get('/activity')
    async def activity():
        from fastapi.responses import JSONResponse
        return JSONResponse(call(service.activity.snapshot), headers={'Cache-Control': 'no-store'})

    @router.get('/notes')
    async def notes(query: str = ''):
        from fastapi.responses import JSONResponse
        return JSONResponse({'items': call(service.notes.search, query)}, headers={'Cache-Control': 'no-store'})

    @router.post('/notes')
    async def create_note(request: Request):
        return call(service.notes.create, **await payload(request))

    # Static paths must precede the note-id route.
    @router.get('/notes/selection')
    async def note_selection():
        from fastapi.responses import JSONResponse
        return JSONResponse(call(service.notes.selection), headers={'Cache-Control': 'no-store'})

    @router.post('/notes/selection')
    async def select_note(request: Request):
        data = await payload(request)
        if set(data) != {'note_id', 'version', 'expected_revision'}:
            raise HTTPException(400, 'note_selection_fields_required')
        return call(service.notes.select, **data)

    @router.get('/notes/{note_id}/history')
    async def note_history(note_id: str):
        from fastapi.responses import JSONResponse
        return JSONResponse({'items': call(service.notes.history, note_id)}, headers={'Cache-Control': 'no-store'})

    @router.get('/notes/{note_id}')
    async def read_note(note_id: str, version: int | None = None):
        from fastapi.responses import JSONResponse
        result = call(service.notes.get, note_id) if version is None else call(service.notes.get_version, note_id, version)
        return JSONResponse(result, headers={'Cache-Control': 'no-store'})

    @router.patch('/notes/{note_id}')
    async def update_note(note_id: str, request: Request):
        return call(service.notes.update, note_id, **await payload(request))

    @router.post('/notes/{note_id}/delete')
    async def delete_note(note_id: str, request: Request):
        result = call(service.notes.delete, note_id, **await payload(request))
        proposals = getattr(service, 'note_proposals', None)
        if proposals is not None:
            proposals.discard_note(note_id)
        return result

    @router.get('/interaction/preferences')
    async def interaction_preferences():
        return {'items': service.preference_store.list_claims(include_inactive=True)}

    @router.get('/interaction/evidence/{evidence_id}')
    async def read_evidence(evidence_id: str, request: Request):
        origin = request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403, 'cross_origin_read_denied')
        from fastapi.responses import JSONResponse
        return JSONResponse(call(service.preference_store.get_evidence, evidence_id), headers={'Cache-Control': 'no-store'})

    @router.post('/interaction/evidence')
    async def add_evidence(request: Request):
        data = await payload(request)
        if set(data) - {'text'}:
            raise HTTPException(400, 'only_user_text_allowed')
        return call(service.preference_store.add_evidence, data.get('text'), source='explicit_user', subject='user')

    @router.post('/interaction/preferences')
    async def add_preference(request: Request):
        data = await payload(request)
        if set(data) - {'dimension', 'value', 'evidence_ids', 'scope', 'valid_until'}:
            raise HTTPException(400, 'unknown_preference_fields')
        return call(service.preference_store.create_claim, **data)

    @router.post('/interaction/preferences/{claim_id}/revise')
    async def revise_preference(claim_id: str, request: Request):
        data = await payload(request)
        return call(service.preference_store.revise_claim, claim_id, **data)

    @router.post('/interaction/preferences/{claim_id}/delete')
    async def delete_preference(claim_id: str, request: Request):
        await payload(request)
        return {'deleted': call(service.preference_store.delete_claim, claim_id)}

    @router.post('/interaction/evidence/{evidence_id}/delete')
    async def delete_evidence(evidence_id: str, request: Request):
        await payload(request)
        return {'affected': call(service.preference_store.delete_evidence, evidence_id)}

    @router.get('/tasks')
    async def tasks():
        return {'items': service.store.list_tasks()}

    @router.post('/tasks')
    async def create_task(request: Request):
        data = await payload(request)
        return call(service.store.create_task, **data)

    @router.patch('/tasks/{task_id}')
    async def update_task(task_id: str, request: Request):
        return call(service.store.update_task, task_id, await payload(request))

    @router.get('/reminders')
    async def reminders():
        return {'items': service.store.list_reminders()}

    @router.post('/reminders')
    async def create_reminder(request: Request):
        return call(service.store.create_reminder, **await payload(request))

    @router.post('/reminders/{reminder_id}/cancel')
    async def cancel_reminder(reminder_id: str, request: Request):
        await payload(request)
        return call(service.store.cancel_reminder, reminder_id)

    @router.post('/reminders/{reminder_id}/snooze')
    async def snooze_reminder(reminder_id: str, request: Request):
        data = await payload(request)
        return call(service.store.snooze_reminder, reminder_id, data.get('scheduled_at'))

    @router.get('/preferences/proactivity')
    async def preferences():
        return service.store.preferences()

    @router.patch('/preferences/proactivity')
    async def set_preferences(request: Request):
        return call(service.store.set_preferences, await payload(request))

    @router.get('/notifications')
    async def notifications():
        return {'items': service.store.list_notifications()}

    @router.post('/notifications/{notification_id}/ack')
    async def ack(notification_id: str, request: Request):
        await payload(request)
        return call(service.store.ack_notification, notification_id)

    @router.post('/notifications/{notification_id}/dismiss')
    async def dismiss(notification_id: str, request: Request):
        await payload(request)
        return call(service.store.dismiss_notification, notification_id)

    @router.post('/assistant/run')
    async def run(request: Request):
        data = await payload(request)
        text = data.get('text')
        if not isinstance(text, str) or not text.strip() or len(text) > 12000:
            raise HTTPException(400, 'invalid_text')
        return await service.start(text, str(data.get('session_id', '')))

    @router.get('/assistant/runs/{run_id}')
    async def run_state(run_id: str):
        result = call(service.runtime.get, run_id)
        return {**result, 'events': service.runtime.events(run_id)}

    @router.post('/assistant/runs/{run_id}/confirm')
    async def confirm(run_id: str, request: Request):
        data = await payload(request)
        if type(data.get('approve')) is not bool or not isinstance(data.get('confirmation_id'), str):
            raise HTTPException(400, 'invalid_confirmation')
        try:
            return await service.resume(run_id, data['confirmation_id'], data['approve'])
        except KeyError:
            raise HTTPException(404, 'object_not_found')
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post('/assistant/runs/{run_id}/continue')
    async def continue_run(run_id: str):
        """从中断处继续推进计划（不重新开始，不重放已成功的步骤）。"""
        try:
            return await service.run_plan(run_id)
        except KeyError:
            raise HTTPException(404, 'object_not_found')
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    @router.post('/assistant/runs/{run_id}/revise')
    async def revise_run(run_id: str, request: Request):
        """用户改目标 / 否决某步 / 增删步骤：更新计划并留下变更记录。"""
        data = await payload(request)
        goal = data.get('goal')
        drop = data.get('drop_steps')
        add = data.get('add_steps')
        note = data.get('note')
        if goal is not None and (not isinstance(goal, str) or not 0 < len(goal) <= 12000):
            raise HTTPException(400, 'invalid_goal')
        if drop is not None and (not isinstance(drop, list)
                                 or any(not isinstance(item, str) or not item for item in drop)):
            raise HTTPException(400, 'invalid_drop_steps')
        if add is not None and (not isinstance(add, list) or len(add) > 6
                                or any(not isinstance(item, dict) for item in add)):
            raise HTTPException(400, 'invalid_add_steps')
        if note is not None and (not isinstance(note, str) or len(note) > 200):
            raise HTTPException(400, 'invalid_note')
        try:
            return await service.revise_run_plan(run_id, goal=goal, drop_steps=drop,
                                                 add_steps=add, note=note or '')
        except KeyError:
            raise HTTPException(404, 'object_not_found')
        except ValueError as exc:
            raise HTTPException(409, str(exc))

    return router


async def scheduler(service):
    """No model call for deterministic reminders. Notifications survive disconnect."""
    while True:
        service.store.scan_due()
        await asyncio.sleep(2)
