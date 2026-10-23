"""世界意图端点的离线测试。

不联网：`create_world_intent_router(llm=...)` 接受注入的假客户端，
所以这里能确定性地验证三条边界——

  1. 没 Key 时返回 503，不返回猜的意图。
  2. 模型输出裹了代码块 / 前言时能抠出 JSON；抠不出就是 502。
  3. 提示词里的模板表来自请求，不来自服务端的硬编码。

用法：.venv/Scripts/python.exe scripts/test_world_intent.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from server.world_intent import (  # noqa: E402
    TemplateIn,
    build_prompt,
    create_world_intent_router,
)

PASSED = 0
FAILED = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global PASSED, FAILED
    if condition:
        PASSED += 1
        print(f"  ok   {name}")
    else:
        FAILED += 1
        print(f"  FAIL {name}" + (f" —— {detail}" if detail else ""))


class FakeLLM:
    """假客户端：`offline` 与 `complete` 都可控。"""

    def __init__(self, reply: str | Exception = '{"kind":"complete"}', offline: bool = False):
        self.reply = reply
        self.offline = offline
        self.calls: list[list[dict[str, str]]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def complete(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


def client_for(fake: FakeLLM) -> TestClient:
    app = FastAPI()
    app.include_router(create_world_intent_router(llm=fake))  # type: ignore[arg-type]
    return TestClient(app)


TEMPLATES = [
    {"id": "workshop_plot", "displayName": "新工坊", "boundTask": None},
    {"id": "crawler_mine", "displayName": "爬虫矿洞", "boundTask": "research"},
]


def test_offline_refuses() -> None:
    print("\n[无 Key]")
    fake = FakeLLM(offline=True)
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "把报告弄一下", "templates": []})
    check("返回 503 而不是猜一个意图", response.status_code == 503, str(response.status_code))
    check("说清是缺 Key", "LLM_API_KEY" in response.json()["detail"])
    check("没有真的调用模型", len(fake.calls) == 0)


def test_happy_path() -> None:
    print("\n[正常路径]")
    fake = FakeLLM(json.dumps({"kind": "dispatch", "taskKind": "writing", "summary": "弄一下报告"}))
    client = client_for(fake)
    response = client.post(
        "/api/world/intent",
        json={"utterance": "把那份季度报告弄一下", "templates": TEMPLATES},
    )
    check("200", response.status_code == 200, response.text)
    body = response.json()
    check(
        "原样透传意图",
        body["intent"] == {"kind": "dispatch", "taskKind": "writing", "summary": "弄一下报告"},
    )
    check("标注来源为 model", body["source"] == "model")
    check("温度给 0", fake.kwargs[0].get("temperature") == 0.0)
    check("开了 json_mode", fake.kwargs[0].get("json_mode") is True)
    check("用户原话没被改写", fake.calls[0][-1]["content"] == "把那份季度报告弄一下")


def test_fenced_json() -> None:
    print("\n[模型输出不干净]")
    fake = FakeLLM('```json\n{"kind":"unknown","reason":"闲聊"}\n```')
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "今天好累", "templates": []})
    check("能抠出代码块里的 JSON", response.status_code == 200, response.text)
    check("内容正确", response.json()["intent"]["kind"] == "unknown")


def test_preamble_json() -> None:
    print("\n[带前言]")
    fake = FakeLLM('好的，这是结果：{"kind":"setWeather","weather":"rain"}')
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "下雨吧", "templates": []})
    check("能从全文中抠出 JSON 对象", response.status_code == 200, response.text)


def test_bad_json() -> None:
    print("\n[抠不出 JSON]")
    fake = FakeLLM("我觉得应该下雨")
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "下雨吧", "templates": []})
    check("502 而不是编一个", response.status_code == 502, str(response.status_code))
    check("说清没有返回 JSON", "JSON" in response.json()["detail"])


def test_upstream_error() -> None:
    print("\n[上游失败]")
    fake = FakeLLM(RuntimeError("connect timeout"))
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "把报告弄一下", "templates": []})
    check("502", response.status_code == 502, str(response.status_code))
    check("把原因带出来", "connect timeout" in response.json()["detail"])


def test_prompt_carries_templates() -> None:
    print("\n[提示词由请求驱动]")
    prompt = build_prompt([TemplateIn(**t) for t in TEMPLATES])
    check("模板 id 在提示词里", "crawler_mine" in prompt)
    check("显示名在提示词里", "爬虫矿洞" in prompt)
    check("不接活的模板被如实说明", "新工坊，不接活" in prompt)

    empty = build_prompt([])
    check("模板表为空时明说不能建", "没有可建的模板" in empty)
    check("模板表为空时提示词里没有伪造的模板", "workshop_plot" not in empty)


def test_validation() -> None:
    print("\n[请求校验]")
    fake = FakeLLM()
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "", "templates": []})
    check("空句子被拒", response.status_code == 422, str(response.status_code))
    response = client.post("/api/world/intent", json={"templates": []})
    check("缺 utterance 被拒", response.status_code == 422, str(response.status_code))


def main() -> int:
    print("世界意图端点 · 离线测试")
    test_offline_refuses()
    test_happy_path()
    test_fenced_json()
    test_preamble_json()
    test_bad_json()
    test_upstream_error()
    test_prompt_carries_templates()
    test_validation()
    print(f"\n=== {PASSED} passed, {FAILED} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
