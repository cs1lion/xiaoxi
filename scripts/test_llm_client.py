"""LLM 客户端的离线契约测试。

为什么值得单独立一个测试：`server/llm.py` 是全项目唯一的模型出口，而它今年
踩过的三个坑都不在"返回了字符串"这一层——

  1. 推理模型把 token 预算吃光，`finish_reason=length` 且正文为空。只看 text
     会把"被截断"读成"模型没话说"（2026-10-17 实测：900 预算下 1/18 轮空正文）。
  2. `complete_json` 的解析重试：模型偶尔在 JSON 外面包一句话，第一版直接
     `json.loads` 抛异常，把慢环任务带崩。
  3. Windows 上 `subprocess` 找不到 `npm`（那是 shell 脚本）——同类问题在
     任何"解析可执行文件路径"的地方都会复发。

所以这里钉的是**元数据与降级路径**，不是网络行为：全部用假 transport，不联网、
不需要 Key、确定。

    python scripts/test_llm_client.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 隔离：导入任何 server 单例之前钉死内存库与临时目录。
import os
os.environ["DB_PATH"] = ":memory:"

from server.config import settings  # noqa: E402
settings.db_path = ":memory:"

from server.llm import LLM, LLMError, _loose_json, _strip_fence  # noqa: E402
from types import SimpleNamespace  # noqa: E402

passed = 0
failed: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f" — {detail}" if detail else ""))


class FakeResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status_code = status

    def json(self):
        return self._payload


class FakeClient:
    """记录请求体，按脚本返回响应。不发任何网络包。

    `responses` 里可以是 dict（包成 200）、int（包成该状态码的空响应），
    或 Exception（抛出，模拟连接层故障）。
    """

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    async def post(self, url, json=None, **kwargs):
        self.calls.append({"url": url, "json": json})
        item = self.responses.pop(0) if self.responses else {"choices": []}
        if isinstance(item, Exception):
            raise item
        if isinstance(item, int):
            return FakeResponse({"error": {"message": "upstream detail"}}, status=item)
        return FakeResponse(item)

    async def aclose(self):
        return None


def live_config() -> SimpleNamespace:
    return SimpleNamespace(
        llm_provider="openai",
        llm_base_url="https://example.invalid/v1",
        llm_api_key="test-key",
        llm_model="test-model",
        llm_temperature=0.9,
        llm_max_tokens=400,
        llm_reply_tokens=1200,
        llm_reasoning_effort="low",
        llm_frequency_penalty=0,
        llm_presence_penalty=0,
    )


async def with_client(llm: LLM, client: FakeClient):
    llm._client = client
    return llm


def test_offline_mode_needs_no_network() -> None:
    print("[offline]")
    import asyncio

    cfg = SimpleNamespace(llm_provider="mock", llm_base_url="", llm_api_key="",
                          llm_model="m", llm_temperature=0.9, llm_max_tokens=400,
                          llm_reply_tokens=900, llm_reasoning_effort="",
                          llm_frequency_penalty=0, llm_presence_penalty=0)
    llm = LLM(config=cfg)
    check("无 Key 时 offline=True", llm.offline is True)
    raw = asyncio.run(llm.complete_raw([{"role": "user", "content": "在吗"}]))
    check("offline 返回占位文本", bool(raw["text"].strip()), raw)
    check("offline 带 finish_reason", raw["finish_reason"] == "stop", raw)


def test_metadata_survives_empty_text() -> None:
    """空正文 + length 必须能和一个正常短回复区分开。"""
    print("[metadata]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": ""}, "finish_reason": "length"}],
         "usage": {"completion_tokens": 1200}},
        {"choices": [{"message": {"content": "嗯。"}, "finish_reason": "stop"}],
         "usage": {"completion_tokens": 12}},
    ])
    asyncio.run(with_client(llm, client))
    truncated = asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}]))
    check("截断时正文为空", truncated["text"] == "", truncated)
    check("截断时 finish_reason=length", truncated["finish_reason"] == "length", truncated)
    check("截断时 usage 仍上报", truncated["usage"]["completion_tokens"] == 1200, truncated)
    ok = asyncio.run(llm.complete_raw([{"role": "user", "content": "b"}]))
    check("正常回复 finish_reason=stop", ok["finish_reason"] == "stop", ok)
    check("两种情形可区分", (truncated["text"], truncated["finish_reason"])
          != (ok["text"], ok["finish_reason"]))


def test_reasoning_field_is_kept_separately() -> None:
    print("[reasoning]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": "答案", "reasoning": "我在想"},
                      "finish_reason": "stop"}], "usage": {}},
    ])
    asyncio.run(with_client(llm, client))
    raw = asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}]))
    check("reasoning 与 content 分开", raw["text"] == "答案" and raw["reasoning"] == "我在想", raw)


def test_complete_json_recovers_from_wrapped_output() -> None:
    print("[json]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": "好的，结果是：{\"a\": 1}"},
                      "finish_reason": "stop"}], "usage": {}},
    ])
    asyncio.run(with_client(llm, client))
    value = asyncio.run(llm.complete_json(
        [{"role": "system", "content": "只输出 JSON"}, {"role": "user", "content": "x"}]))
    check("JSON 外面包话也能解析", value == {"a": 1}, value)
    check("指令里追加了纯 JSON 要求",
          "只输出 JSON" in client.calls[0]["json"]["messages"][0]["content"], client.calls[0])


def test_complete_json_retries_then_raises() -> None:
    print("[json retry]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": "完全不是 JSON"}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "还是不是"}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": "仍然不是"}, "finish_reason": "stop"}]},
    ])
    asyncio.run(with_client(llm, client))
    try:
        asyncio.run(llm.complete_json(
            [{"role": "system", "content": "S"}, {"role": "user", "content": "x"}], retries=2))
        raised = False
    except LLMError:
        raised = True
    check("三次都失败时抛 LLMError", raised)
    check("默认重试次数为 3 次请求", len(client.calls) == 3, len(client.calls))


def test_4xx_is_not_retried() -> None:
    """4xx 重试三次只是把限流拖成限流；且错误体可能回显凭据。"""
    print("[http]")
    import asyncio

    llm = LLM(config=live_config())
    # 三个 400：若实现误判成可重试，这里会全部消耗掉。
    client = FakeClient([400, 400, 400])
    asyncio.run(with_client(llm, client))
    try:
        asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}]))
        raised = False
        message = ""
    except LLMError as exc:
        raised = True
        message = str(exc)
    check("4xx 抛 LLMError", raised)
    check("错误信息只有状态码，不带上游响应体",
          "HTTP 400" in message and "upstream detail" not in message, message)
    check("4xx 只请求一次", len(client.calls) == 1, len(client.calls))


def test_5xx_is_retried_then_raises() -> None:
    print("[http 5xx]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([500, 500, 500])
    asyncio.run(with_client(llm, client))
    try:
        asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}], max_attempts=3))
        raised = False
    except LLMError:
        raised = True
    check("5xx 三次后抛 LLMError", raised)
    check("5xx 请求满三次", len(client.calls) == 3, len(client.calls))


def test_penalty_fields_stay_off_by_default() -> None:
    print("[payload]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]},
    ])
    asyncio.run(with_client(llm, client))
    asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}]))
    body = client.calls[0]["json"]
    check("默认不下发 frequency_penalty", "frequency_penalty" not in body, body)
    check("默认不下发 presence_penalty", "presence_penalty" not in body, body)
    check("下发 reasoning_effort", body.get("reasoning_effort") == "low", body)


def test_reasoning_effort_override_reaches_payload() -> None:
    print("[effort]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([
        {"choices": [{"message": {"content": "x"}, "finish_reason": "stop"}]},
    ])
    asyncio.run(with_client(llm, client))
    asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}],
                                 reasoning_effort="minimal", max_tokens=600))
    body = client.calls[0]["json"]
    check("单次可覆盖推理强度", body.get("reasoning_effort") == "minimal", body)
    check("单次可覆盖 token 预算", body.get("max_tokens") == 600, body)


def test_strip_and_loose_json() -> None:
    print("[parsing]")
    check("剥掉 ```json 围栏", _strip_fence('```json\n{"a":1}\n```') == '{"a":1}')
    check("剥掉裸围栏", _strip_fence('```\n{"a":1}\n```') == '{"a":1}')
    check("普通文本原样", _strip_fence('{"a":1}') == '{"a":1}')
    check("从夹带文字里抠出对象", _loose_json('前言 {"a":1} 后记') == {"a": 1})
    check("抠出数组", _loose_json('结果：[1,2]') == [1, 2])
    try:
        _loose_json("完全没有 JSON")
        raised = False
    except json.JSONDecodeError:
        raised = True
    check("没有 JSON 时抛 JSONDecodeError", raised)


def test_missing_choices_fails_loudly() -> None:
    """响应里没有 choices 时必须是可诊断的错误，不是 IndexError 穿透到调用方。"""
    print("[http shape]")
    import asyncio

    llm = LLM(config=live_config())
    client = FakeClient([{"unexpected": True}])
    asyncio.run(with_client(llm, client))
    try:
        asyncio.run(llm.complete_raw([{"role": "user", "content": "a"}]))
        raised = False
        name = ""
    except LLMError as exc:
        raised = True
        name = str(exc)
    check("坏形状抛 LLMError", raised)
    check("错误信息点名失败类型", "IndexError" in name or "KeyError" in name, name)


def main() -> int:
    test_offline_mode_needs_no_network()
    test_metadata_survives_empty_text()
    test_reasoning_field_is_kept_separately()
    test_complete_json_recovers_from_wrapped_output()
    test_complete_json_retries_then_raises()
    test_4xx_is_not_retried()
    test_penalty_fields_stay_off_by_default()
    test_reasoning_effort_override_reaches_payload()
    test_strip_and_loose_json()
    test_5xx_is_retried_then_raises()
    test_missing_choices_fails_loudly()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        print("失败项：" + "、".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
