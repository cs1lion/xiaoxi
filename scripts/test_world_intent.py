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
    """假客户端：`offline`、正文与 finish_reason 都可控。"""

    def __init__(
        self,
        reply: str | Exception = '{"kind":"complete"}',
        offline: bool = False,
        finish_reason: str = "stop",
    ):
        self.reply = reply
        self.offline = offline
        self.finish_reason = finish_reason
        self.calls: list[list[dict[str, str]]] = []
        self.kwargs: list[dict[str, Any]] = []

    async def complete_raw(self, messages, **kwargs):
        self.calls.append(messages)
        self.kwargs.append(kwargs)
        if isinstance(self.reply, Exception):
            raise self.reply
        return {"text": self.reply, "finish_reason": self.finish_reason,
                "usage": {}, "reasoning": ""}


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


def test_truncated_output() -> None:
    print("\n[输出被截断]")
    # 实测踩过：max_tokens 太小时正文被截成 '{"kind":"dispatch'
    fake = FakeLLM('{"kind":"dispatch', finish_reason="length")
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "帮我写个爬虫", "templates": []})
    check("502", response.status_code == 502, str(response.status_code))
    detail = response.json()["detail"]
    check("说清是截断，不是\"没有返回 JSON\"", "截断" in detail, detail)
    check("把 token 预算带出来，方便定位", "max_tokens" in detail, detail)


def test_empty_output() -> None:
    print("\n[模型没产出正文]")
    # 实测踩过：推理模型把预算全花在思考上，正文为空。
    fake = FakeLLM("", finish_reason="length")
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "帮我写个爬虫", "templates": []})
    check("502", response.status_code == 502, str(response.status_code))
    check("说清是没有正文", "没有产出正文" in response.json()["detail"])


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


CONTEXT = {
    "built": {
        "town": [{"templateId": "code_forge", "name": "炼码工坊"}],
        "wild": [{"templateId": "crawler_mine", "name": "爬虫矿洞"}],
    },
    "capacity": [
        {"taskKind": "code", "count": 2},
        {"taskKind": "research", "count": 1},
        {"taskKind": "writing", "count": 0},
    ],
    "busy": [{"name": "莞尔", "taskKind": "code"}],
    "queued": 1,
    "weather": "rain",
    "exhausted": ["code_forge", "crawler_mine"],
}


def test_context_in_prompt() -> None:
    print("\n[世界现状进提示词]")
    from server.world_intent import ContextIn

    prompt = build_prompt([TemplateIn(**t) for t in TEMPLATES], ContextIn(**CONTEXT))
    check("镇内建筑在提示词里", "炼码工坊" in prompt)
    check("郊野建筑在提示词里", "爬虫矿洞" in prompt)
    check("谁在忙写出来了", "莞尔在做写代码" in prompt)
    check("排队数写出来了", "还有 1 条神谕在排队" in prompt)
    check("天气写出来了", "外面在下雨" in prompt)
    check("已建过的模板写出来了", "不能再建第二栋" in prompt)
    # 这几条是实测逼出来的：接上现状后模型一度开始替世界判可行性，
    # 「给我整个新铺子」从 build 退成 unknown（理由："没有指定铺子类型"）。
    check(
        "明确说了现状不改变输出的命令",
        "现状不改变你输出的命令" in prompt,
        "少了这句，模型会拿现状当判据",
    )
    check(
        "明说已有建筑时仍输出 build",
        "仍然照常输出 build" in prompt,
        "要让它把拒绝交给世界，而不是自己提前拒",
    )
    check(
        "分清「打听现状」与「要求做事」",
        "只有用户在问现状时才答 unknown" in prompt,
    )

    # 省略 context 时整段不出现：写「未知」会让模型以为该去猜。
    plain = build_prompt([TemplateIn(**t) for t in TEMPLATES])
    check("没有现状时整段不出现", "世界现在长什么样" not in plain)
    check("没有现状时不写『未知』", "世界状态未知" not in plain)


def test_context_optional_in_request() -> None:
    print("\n[旧客户端不发 context 也能用]")
    fake = FakeLLM('{"kind":"unknown","reason":"闲聊"}')
    client = client_for(fake)
    response = client.post("/api/world/intent", json={"utterance": "今天好累", "templates": []})
    check("不带 context 时 200", response.status_code == 200, str(response.status_code))
    # 带了也要能解析。
    fake2 = FakeLLM('{"kind":"unknown","reason":"闲聊"}')
    client2 = client_for(fake2)
    response2 = client2.post(
        "/api/world/intent",
        json={"utterance": "今天好累", "templates": [], "context": CONTEXT},
    )
    check("带 context 时 200", response2.status_code == 200, str(response2.status_code))
    # FakeLLM 记录的是完整 messages；system 是第一条。
    system = fake2.calls[-1][0]["content"] if fake2.calls else ""
    check(
        "带 context 时它真的进了提示词",
        "炼码工坊" in system,
        "注入的 prompt 里应当有世界现状",
    )


def test_context_rejects_bad_shape() -> None:
    print("\n[现状形状校验]")
    fake = FakeLLM()
    client = client_for(fake)
    # count 为负：世界不可能有负的铺子数，说明请求方算错了。
    bad = {**CONTEXT, "capacity": [{"taskKind": "code", "count": -1}]}
    response = client.post(
        "/api/world/intent",
        json={"utterance": "建个工坊", "templates": [], "context": bad},
    )
    check("负数 count 被拒", response.status_code == 422, str(response.status_code))


def main() -> int:
    print("世界意图端点 · 离线测试")
    test_offline_refuses()
    test_happy_path()
    test_fenced_json()
    test_preamble_json()
    test_bad_json()
    test_upstream_error()
    test_truncated_output()
    test_empty_output()
    test_prompt_carries_templates()
    test_validation()
    test_context_in_prompt()
    test_context_optional_in_request()
    test_context_rejects_bad_shape()
    print(f"\n=== {PASSED} passed, {FAILED} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
