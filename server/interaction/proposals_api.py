"""Candidate editing is not confirmation. Only exact preview versions save."""
from urllib.parse import urlsplit
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from .threads import RevisionConflict


def create_proposals_router(service):
    router = APIRouter(prefix='/api/thread-proposals', tags=['companion'])

    async def data(request, fields):
        origin=request.headers.get('origin')
        if origin and urlsplit(origin).netloc != request.headers.get('host'):
            raise HTTPException(403,'cross_origin_write_denied')
        try:
            value=await request.json()
        except Exception:
            raise HTTPException(400,'invalid_json')
        if not isinstance(value,dict) or set(value)-set(fields):
            raise HTTPException(400,'unknown_fields')
        return value

    def call(fn,*args,**kwargs):
        try:
            return JSONResponse(fn(*args,**kwargs),headers={'Cache-Control':'no-store'})
        except RevisionConflict:
            raise HTTPException(409,'preview_or_thread_changed')
        except KeyError:
            raise HTTPException(404,'proposal_expired_deleted_or_consumed')
        except (TypeError,ValueError) as exc:
            raise HTTPException(400,str(exc))

    @router.get('/{proposal_id}')
    async def get(proposal_id:str):
        return call(service.proposals.get,proposal_id)

    @router.patch('/{proposal_id}')
    async def revise(proposal_id:str,request:Request):
        return call(service.proposals.revise,proposal_id,**await data(request,['expected_version','text']))

    @router.post('/{proposal_id}/confirm')
    async def confirm(proposal_id:str,request:Request):
        return call(service.proposals.confirm,proposal_id,**await data(request,['expected_version']))

    @router.post('/{proposal_id}/reject')
    async def reject(proposal_id:str,request:Request):
        return call(service.proposals.reject,proposal_id,**await data(request,['expected_version']))

    return router
