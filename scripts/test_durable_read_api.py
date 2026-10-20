"""C1 read-only host API: auth, memory tiers, notes/threads, worker catalog.

    python scripts/test_durable_read_api.py
"""
from __future__ import annotations

from types import SimpleNamespace
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.assistant.durable_api import create_durable_router, set_host_token  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def record(**overrides):
    base = dict(id=1, status="active", value="用户喜欢短标题", kind="fact", subject="user",
                source="stated", evidence_state="manual", updated_at=1000.0,
                key="taste.title", confidence=0.9)
    base.update(overrides)
    return SimpleNamespace(**base)


class Memory:
    enabled = True

    def __init__(self):
        self.retriever = SimpleNamespace(search=self.search)

    def search(self, query, top_k=6):
        if query == "不存在":
            return []
        import time
        return [(record(), 0.9),
                (record(id=2, source="inferred", value="可能喜欢短句", updated_at=time.time()), 0.5)][:top_k]


def assistant():
    def read_note(note_id, version):
        if note_id != "n1":
            raise KeyError(note_id)
        return {"body": "便笺正文", "ref": {"type": "note", "id": note_id, "version": version}}

    def read_thread(thread_id, query=""):
        if thread_id != "t1":
            raise KeyError(thread_id)
        return {"id": thread_id, "revision": 2, "query": query}

    roster = {
        "selection": {"max_concurrent_default": 2},
        "agents": [
            {"id": "scribe", "name": "笔录官", "adapter": "durable",
             "summary": "写文档", "strengths": ["写作"], "weaknesses": ["不碰代码"],
             "cost_tier": "cheap", "max_concurrent": 1},
            {"id": "builder", "name": "营造匠", "adapter": "cli_subproc",
             "summary": "写代码", "strengths": ["代码"], "cost_tier": "high",
             "max_concurrent": 2},
        ],
    }
    ledger = SimpleNamespace(open_commissions=lambda: [
        {"id": "c1", "agent_id": "scribe", "status": "running"},
    ])
    executive = SimpleNamespace(roster=roster, ledger=ledger)
    return SimpleNamespace(
        _search_notes=lambda query: {"status": "available", "items": [{"id": "n1", "title": "标题"}]},
        _read_note=read_note,
        _read_thread=read_thread,
        executive=executive,
    )


def main() -> int:
    memory = Memory()
    app = FastAPI()
    app.include_router(create_durable_router(assistant(), memory))
    set_host_token("test-token")
    headers = {"authorization": "Bearer test-token"}
    try:
        with TestClient(app) as client:
            print("[durable read api]")
            check("未带 token 拒绝", client.post("/internal/durable/memory/search",
                                                 json={"query": "标题"}).status_code == 401)

            found = client.post("/internal/durable/memory/search",
                                json={"query": "标题"}, headers=headers)
            body = found.json()
            check("记忆检索 200", found.status_code == 200, str(found.status_code))
            check("stated 分组", len(body.get("stated") or []) == 1, str(body)[:200])
            check("inferred 分组带确认要求",
                  bool(body.get("inferred")) and body["inferred"][0].get("requires_confirmation") is True,
                  str(body.get("inferred"))[:200])
            empty = client.post("/internal/durable/memory/search",
                                json={"query": "不存在"}, headers=headers).json()
            check("空检索返回 not_found", empty.get("status") == "not_found", str(empty))
            check("空检索带负向约束", "没查到不等于没发生" in str(empty.get("note")), str(empty))

            note = client.post("/internal/durable/notes/read",
                               json={"note_id": "n1", "version": 2}, headers=headers)
            check("便笺读 200", note.status_code == 200 and note.json().get("body") == "便笺正文",
                  str(note.status_code))
            check("缺失版本 404", client.post("/internal/durable/notes/read",
                                              json={"note_id": "nope", "version": 1},
                                              headers=headers).status_code == 404)
            thread = client.post("/internal/durable/threads/read",
                                 json={"thread_id": "t1", "query": "标题"}, headers=headers)
            check("事项读 200", thread.status_code == 200 and thread.json().get("id") == "t1",
                  str(thread.status_code))

            workers = client.get("/internal/durable/workers", headers=headers)
            rows = workers.json().get("items") or []
            scribe = next((item for item in rows if item["id"] == "scribe"), {})
            check("Worker 目录有 scribe", scribe.get("adapter") == "durable", str(scribe))
            check("Worker 目录不带 env/executable", "env" not in scribe and "executable" not in scribe,
                  str(scribe))
            status = client.get("/internal/durable/workers/status", headers=headers).json()
            scribe_status = next(item for item in status["items"] if item["id"] == "scribe")
            check("scribe 在飞 1 单", scribe_status["in_flight"] == 1, str(scribe_status))
            check("scribe 容量已满", scribe_status["available"] is False, str(scribe_status))

            memory.enabled = False
            disabled = client.post("/internal/durable/memory/search",
                                   json={"query": "标题"}, headers=headers).json()
            check("关闭记忆返回 disabled", disabled.get("status") == "disabled", str(disabled))
    finally:
        set_host_token(None)

    print()
    if failed:
        print(f"=== {passed} passed, {len(failed)} failed ===")
        for item in failed:
            print(f"  - {item}")
        return 1
    print(f"=== {passed} passed, 0 failed ===")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
