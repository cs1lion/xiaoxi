"""Memory REST routes for the diary and star map (protocol sections 10 and 13).

Same factory pattern as assistant/api.py: the app only needs
``app.include_router(create_diary_router(manager))`` and the starmap twin.
``prefix`` stays the first keyword argument so scripts/check_protocol.py can
statically resolve these routes before the app ever includes them.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException

from .diary import local_date
from .starmap import starmap_graph, starmap_node


def _valid_date(value: str) -> str:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date().isoformat()
    except (TypeError, ValueError):
        raise HTTPException(400, "invalid_date")


def create_diary_router(manager: Any) -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    @router.get("/diary")
    async def list_diary(date: str | None = None, limit: int = 30):
        """Recent first, the last 30 days up to `date` (default today), at most
        `limit` (<=90) rows.

        The list never carries source_message_ids; the detail route owns those.
        """
        anchor = _valid_date(date) if date else local_date()
        limit = max(1, min(int(limit), 90))
        since = (datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=29)).date().isoformat()
        items = [{key: value for key, value in record.items() if key != "source_message_ids"}
                 for record in manager.store.list_diaries(since_date=since, until_date=anchor, limit=limit)]
        return {"items": items, "count": len(items), "enabled": manager.enabled}

    @router.get("/diary/{diary_id}")
    async def read_diary(diary_id: int):
        record = manager.store.get_diary(diary_id)
        if record is None:
            raise HTTPException(404, "diary_not_found")
        return record

    return router


def create_starmap_router(manager: Any) -> APIRouter:
    router = APIRouter(prefix="/api/memory", tags=["memory"])

    @router.get("/starmap")
    async def starmap(days: int = 90, kinds: str = "", q: str = "", limit: int = 400):
        return starmap_graph(manager, days=days, kinds=kinds, q=q, limit=limit)

    @router.get("/starmap/node/{node_id}")
    async def starmap_node_detail(node_id: str):
        record = starmap_node(manager, node_id)
        if record is None:
            raise HTTPException(404, "node_not_found")
        return record

    return router


__all__ = ["create_diary_router", "create_starmap_router"]
