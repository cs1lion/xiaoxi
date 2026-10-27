# -*- coding: utf-8 -*-
"""memory REST 路由不离环门禁（第十批，2026-10-27）。

server/memory/api.py 的四条路由与语音 WS 共享事件循环。同步 SQLite 读会把
循环停住整个读取时长，冻住所有连接。实测（2026-10-27，本机）：3000 条记忆
时一次 /api/memory/starmap 要 202ms 循环时间，其中 91ms 是全表候选读；
1000 条时 149ms。修复：四条路由的 store 读全部改走 asyncio.to_thread，与
#31 检索卸载、executive_route 台账读卸载、scan_due 卸载同一模式。

本门禁钉两条行为：

1. 卸载后载荷不变：路由返回值与直接调 starmap_graph / list_diaries 逐值
   相等，404 路径保持 404；
2. 路由真让出循环：请求在飞时心跳任务能持续推进。判据是调度次数不是墙钟
   ——同步版会把循环停住整个读取时长，心跳只能跑个位数次；to_thread 版
   在读取期间让心跳跑几千次。阈值取 10，两个方向都有数量级余量。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="mem-api-", suffix=".db"))

from fastapi import HTTPException  # noqa: E402

from server.memory import starmap as starmap_mod  # noqa: E402
from server.memory.api import create_diary_router, create_starmap_router  # noqa: E402
from server.memory.diary import local_date  # noqa: E402
from server.memory.store import MemoryStore  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

HEARTBEAT_FLOOR = 10
STARMAP_ROWS = 600


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class _Manager:
    """starmap_graph 只读 enabled 与 store 两个属性。"""

    enabled = True

    def __init__(self, store: MemoryStore) -> None:
        self.store = store


def _endpoint(router: Any, path: str):
    for route in router.routes:
        if getattr(route, "path", None) == path:
            return route.endpoint
    raise AssertionError(f"route not found: {path}")


def seed(store: MemoryStore, count: int) -> None:
    for i in range(count):
        store.upsert(kind="fact", key=f"路由:{i:04d}",
                     value=f"第{i}条记忆内容，供路由门禁播种使用",
                     source="stated", importance=0.3, session_id="gate",
                     evidence_state="manual")


def expect_404(name: str, coro_factory) -> None:
    try:
        asyncio.run(coro_factory())
        check(name, False, "未抛异常")
    except HTTPException as exc:
        check(name, exc.status_code == 404, f"status={exc.status_code}")


def main() -> int:
    print("[卸载后载荷不变：路由返回值 == 直接调用]")
    store = MemoryStore(tempfile.mktemp(prefix="mem-api-", suffix=".db"))
    manager = _Manager(store)
    seed(store, 6)

    starmap_router = create_starmap_router(manager)
    diary_router = create_diary_router(manager)
    starmap_ep = _endpoint(starmap_router, "/api/memory/starmap")
    node_ep = _endpoint(starmap_router, "/api/memory/starmap/node/{node_id}")
    diary_ep = _endpoint(diary_router, "/api/memory/diary")
    diary_detail_ep = _endpoint(diary_router, "/api/memory/diary/{diary_id}")

    direct_graph = starmap_mod.starmap_graph(manager)
    via_route = asyncio.run(starmap_ep(days=90, kinds="", q="", limit=400))
    check("starmap 路由载荷与直接调用逐值相等", via_route == direct_graph,
          f"nodes={len(via_route.get('nodes', []))} vs {len(direct_graph.get('nodes', []))}")

    anchor = local_date()
    since = (datetime.strptime(anchor, "%Y-%m-%d") - timedelta(days=29)).date().isoformat()
    direct_items = [{key: value for key, value in record.items() if key != "source_message_ids"}
                    for record in store.list_diaries(since_date=since, until_date=anchor, limit=30)]
    via_diary = asyncio.run(diary_ep(date=None, limit=30))
    check("diary 路由载荷与直接调用逐值相等",
          via_diary == {"items": direct_items, "count": len(direct_items), "enabled": True},
          f"count={via_diary.get('count')} vs {len(direct_items)}")

    print("[404 路径保持]")
    expect_404("不存在的日记 404", lambda: diary_detail_ep(999999))
    expect_404("不存在的星图节点 404", lambda: node_ep("mem:999999"))

    print("[路由让出循环：请求在飞时心跳持续推进]")
    big_store = MemoryStore(tempfile.mktemp(prefix="mem-api-", suffix=".db"))
    big_manager = _Manager(big_store)
    seed(big_store, STARMAP_ROWS)
    big_ep = _endpoint(create_starmap_router(big_manager), "/api/memory/starmap")
    beats = {"n": 0}

    async def run_with_heartbeat() -> None:
        done = asyncio.Event()

        async def heartbeat() -> None:
            while not done.is_set():
                beats["n"] += 1
                await asyncio.sleep(0)

        task = asyncio.create_task(heartbeat())
        try:
            await big_ep(days=90, kinds="", q="", limit=400)
        finally:
            done.set()
            await task

    asyncio.run(run_with_heartbeat())
    check(f"请求在飞期间心跳持续推进（>= {HEARTBEAT_FLOOR} 次，路由未独占循环）",
          beats["n"] >= HEARTBEAT_FLOOR, f"beats={beats['n']}")

    store.close()
    big_store.close()
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
