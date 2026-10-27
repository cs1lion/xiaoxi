# -*- coding: utf-8 -*-
"""REST 路由器同步 service 写不离环门禁（第二十三批，2026-10-27）。

第二十一批清点出的第 1-2 项：两个 async 路由器的写端点原先在 async
handler 里经同步 call() 助手直接调 service 写方法，整段跑在事件循环
上。按第十六批判据（写事务一律卸，BEGIN IMMEDIATE + commit + fsync
实测家族 4-18ms）改走 asyncio.to_thread 后，本门禁钉住「service 写
调用跑在工作线程，不在循环线程」。

覆盖 6 个写端点：
- /api/thread-proposals：revise / confirm / reject
- /api/note-proposals：revise / confirm / reject

editor-insights 的 dismiss/feedback 按判据**不卸**：它们是纯内存写
（_sessions OrderedDict，无 SQLite、无 fsync、亚毫秒），且 dismiss 的
_revoke 会取消 analyze 的 asyncio.Task——卸到 worker 线程就成了跨线程
task.cancel()（官方非线程安全）。第二十三批 review 抓出这条，已恢复
同步。读端点（get / list_pending / status / state）按判据留同步，不在
本门禁范围。判据是线程身份：同步版恒在循环线程，to_thread 版恒在
asyncio_* worker，双向无灰区，故可进 ALWAYS_REQUIRED。

路由器的同源校验在无 Origin 头时放行（本地脚本/直连路径），门禁不
伪造 Origin，走的就是这条真实分支。
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import threading
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", tempfile.mktemp(prefix="router-offloop-", suffix=".db"))

import httpx  # noqa: E402
from fastapi import FastAPI  # noqa: E402

from server.assistant.note_proposals_api import create_note_proposals_router  # noqa: E402
from server.interaction.proposals_api import create_proposals_router  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []

WRITES = [
    ("PATCH", "/api/thread-proposals/x1",
     {"expected_version": 1, "text": "改后的预览"}, "proposals.revise"),
    ("POST", "/api/thread-proposals/x1/confirm",
     {"expected_version": 1}, "proposals.confirm"),
    ("POST", "/api/thread-proposals/x1/reject",
     {"expected_version": 1}, "proposals.reject"),
    ("PATCH", "/api/note-proposals/x1",
     {"expected_version": 1, "title": "更短的标题", "body": "更短的正文"},
     "note_proposals.revise"),
    ("POST", "/api/note-proposals/x1/confirm",
     {"expected_version": 1}, "note_proposals.confirm"),
    ("POST", "/api/note-proposals/x1/reject",
     {"expected_version": 1}, "note_proposals.reject"),
]


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class WriteRecorder:
    """service 替身：每个写方法记录 (名字, 线程) 后返回可 JSON 化的值。

    只替身写方法；读方法给个空壳即可（本门禁不驱动读端点）。
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, threading.Thread]] = []

    def _record(self, name: str) -> dict[str, object]:
        self.calls.append((name, threading.current_thread()))
        return {"id": "x1", "ok": True}


def build_service(recorder: WriteRecorder) -> SimpleNamespace:
    proposals = SimpleNamespace(
        revise=lambda *a, **kw: recorder._record("proposals.revise"),
        confirm=lambda *a, **kw: recorder._record("proposals.confirm"),
        reject=lambda *a, **kw: recorder._record("proposals.reject"),
    )
    note_proposals = SimpleNamespace(
        revise=lambda *a, **kw: recorder._record("note_proposals.revise"),
        confirm=lambda *a, **kw: recorder._record("note_proposals.confirm"),
        reject=lambda *a, **kw: recorder._record("note_proposals.reject"),
    )
    return SimpleNamespace(
        proposals=proposals,
        note_proposals=note_proposals,
    )


def build_app(service: SimpleNamespace) -> FastAPI:
    app = FastAPI()
    app.include_router(create_proposals_router(service))
    app.include_router(create_note_proposals_router(service))
    return app


async def drive_writes(client: httpx.AsyncClient) -> None:
    for method, path, payload, _name in WRITES:
        response = await client.request(method, path, json=payload)
        if response.status_code != 200:
            raise AssertionError(f"{method} {path} -> {response.status_code}: {response.text}")


def main() -> int:
    recorder = WriteRecorder()
    app = build_app(build_service(recorder))

    async def run() -> None:
        transport = httpx.ASGITransport(app=app)
        # 两个路由器的同源校验在无 Origin 头时放行（本地脚本/直连路径），
        # 门禁不伪造 Origin，走的就是这条真实分支。
        async with httpx.AsyncClient(transport=transport,
                                     base_url="http://testserver") as client:
            await drive_writes(client)

    loop_thread = threading.current_thread()
    asyncio.run(run())

    names = [name for name, _thread in recorder.calls]
    expected = [name for _m, _p, _payload, name in WRITES]
    check("6 个写端点的 service 调用全部发生且次序正确",
          names == expected, f"got={names!r} want={expected!r}")
    on_loop = [name for name, thread in recorder.calls if thread is loop_thread]
    check("全部 service 写跑在工作线程（不在循环线程）",
          not on_loop, repr(on_loop))

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
