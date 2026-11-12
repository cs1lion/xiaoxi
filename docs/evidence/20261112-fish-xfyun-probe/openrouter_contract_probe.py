# -*- coding: utf-8 -*-
"""OpenRouter 端点与项目 llm.py 契约的逐字段对齐 + 余额/限额探测。"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

KEY = os.environ.get("OR_KEY", "")
URL = "https://openrouter.ai/api/v1/chat/completions"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json",
     "HTTP-Referer": "https://github.com/cs1lion/xiaoxi", "X-Title": "xiaoxi"}

SYSTEM = (
    "你是小溪，一个有性格的中文语音陪伴搭档。你的语气自然随意，像熟人聊天，"
    "不要播音腔，不刻意表演。你有分档记忆检索的结果，可能包含明确说过、推测、"
    "不确定三档，也可能为空。检索为空时不要用常识脑补共同经历。"
)
USER = "我昨天加班到现在才弄完那个破项目，你说我是不是该跳槽了？"

rows = []


def call(label, body, *, timeout=180, stream=False):
    t0 = time.perf_counter()
    if stream:
        first = None
        text = ""
        usage = None
        with httpx.stream("POST", URL, json=body, headers=H, timeout=timeout) as r:
            status = r.status_code
            for line in r.iter_lines():
                if not line.startswith("data: ") or line == "data: [DONE]":
                    continue
                ev = json.loads(line[6:])
                if first is None:
                    first = (time.perf_counter() - t0) * 1000
                c = ev.get("choices", [{}])[0].get("delta", {}).get("content")
                if c:
                    text += c
                if ev.get("usage"):
                    usage = ev["usage"]
        ms = (time.perf_counter() - t0) * 1000
        print(f"  {label:38s} HTTP {status}  首字 {round(first or 0):5d}ms  完 {round(ms):6d}ms  "
              f"content {len(text)}ch  usage={usage}")
        rows.append({"label": label, "status": status, "ttft_ms": round(first or 0),
                     "total_ms": round(ms), "content_len": len(text), "usage": usage,
                     "preview": text[:150]})
        return
    r = httpx.post(URL, json=body, headers=H, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        print(f"  {label:38s} HTTP {r.status_code}  {round(ms):6d}ms  {r.text[:140]}")
        rows.append({"label": label, "status": r.status_code, "body": r.text[:200]})
        return
    d = r.json()
    ch = d["choices"][0]
    msg = ch["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
    print(f"  {label:38s} HTTP {r.status_code}  {round(ms):6d}ms  content {len(content)}ch  "
          f"reasoning {len(reasoning)}ch  finish={ch.get('finish_reason')}  "
          f"model={d.get('model')}  cost={d.get('usage', {}).get('cost')}")
    rows.append({"label": label, "status": r.status_code, "ms": round(ms),
                 "content_len": len(content), "reasoning_len": len(reasoning),
                 "finish": ch.get("finish_reason"), "model": d.get("model"),
                 "usage": d.get("usage"), "preview": content[:150]})


def main() -> int:
    print("[1] 项目默认形状（openrouter/free 自动路由）")
    call("model/messages/temp/max_tokens=400", {
        "model": "openrouter/free",
        "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.9, "max_tokens": 400})

    print("\n[2] 项目可选字段")
    call("+frequency_penalty=0.2", {
        "model": "openrouter/free", "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.9, "max_tokens": 400, "frequency_penalty": 0.2})
    call("+presence_penalty=0.2", {
        "model": "openrouter/free", "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.9, "max_tokens": 400, "presence_penalty": 0.2})
    call("+response_format=json_object", {
        "model": "openrouter/free", "messages": [{"role": "user", "content": '只回JSON {"a":1}'}],
        "temperature": 0.0, "max_tokens": 400, "response_format": {"type": "json_object"}})
    call("+reasoning_effort=low", {
        "model": "openrouter/free", "messages": [{"role": "user", "content": "你好"}],
        "temperature": 0.9, "max_tokens": 400, "reasoning_effort": "low"})

    print("\n[3] 抽取契约")
    call("extraction-shaped json", {
        "model": "openrouter/free", "temperature": 0.0, "max_tokens": 600,
        "messages": [{"role": "system", "content":
                      '返回 JSON：{"memories":[{"kind":"fact","subject":"user","key":"k","value":"v"}]}'},
                     {"role": "user", "content": "我说我养了一只叫年糕的猫"}],
        "response_format": {"type": "json_object"}})

    print("\n[4] 人格长 prompt（项目真实形态）")
    call("persona prompt, 400 budget", {
        "model": "openrouter/free", "max_tokens": 400,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
        "temperature": 0.9})

    print("\n[5] 流式（项目 llm.py stream 路径）")
    call("stream=true persona prompt", {
        "model": "openrouter/free", "max_tokens": 400, "stream": True,
        "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": USER}],
        "temperature": 0.9}, stream=True)

    print("\n[6] 余额与限额")
    for path in ["/credits", "/key"]:
        try:
            r = httpx.get(f"https://openrouter.ai/api/v1{path}", headers=H, timeout=60)
            print(f"  {path}: HTTP {r.status_code} {r.text[:220]}")
            rows.append({"label": path, "status": r.status_code, "body": r.text[:400]})
        except Exception as exc:
            print(f"  {path}: ERR {type(exc).__name__}")

    print("\n[7] 免费路由实际落到哪些模型（连问 4 次）")
    for i in range(4):
        r = httpx.post(URL, headers=H, timeout=120, json={
            "model": "openrouter/free", "max_tokens": 30,
            "messages": [{"role": "user", "content": "说一个字"}]})
        if r.status_code == 200:
            d = r.json()
            print(f"  第{i+1}次 -> {d.get('model')}  provider={d.get('provider')}  "
                  f"cost={d.get('usage', {}).get('cost')}")
        else:
            print(f"  第{i+1}次 -> HTTP {r.status_code} {r.text[:120]}")

    out = os.path.join(os.environ["TEMP"], "openrouter_contract.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(rows, fh, ensure_ascii=False, indent=2)
    print(f"\n落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
