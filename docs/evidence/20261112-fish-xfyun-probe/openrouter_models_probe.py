# -*- coding: utf-8 -*-
"""OpenRouter 免费模型逐个测：人格 prompt + 400 预算下 content 是否为空。

背景：`openrouter/free` 自动路由落到带 reasoning 的模型时，人格 prompt 会把
400 token 全烧在 reasoning 上，content 恒空、finish=length —— 正是
server/llm.py:110-113 警告的失败形态。

这里逐个固定模型测同一 prompt，看谁能真的返回 content。
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

KEY = os.environ.get("OR_KEY", "")
URL = "https://openrouter.ai/api/v1/chat/completions"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

SYSTEM = (
    "你是小溪，一个有性格的中文语音陪伴搭档。你的语气自然随意，像熟人聊天，"
    "不要播音腔，不刻意表演。你有分档记忆检索的结果，可能包含明确说过、推测、"
    "不确定三档，也可能为空。检索为空时不要用常识脑补共同经历。"
    "回答控制在两三句话以内。"
)
USER = "我昨天加班到现在才弄完那个破项目，你说我是不是该跳槽了？"

MODELS = [
    "openrouter/free",
    "baidu/ernie-5.5-flash-preview:free",
    "google/gemma-4-31b-it:free",
    "google/gemma-4-26b-a4b-it:free",
    "nvidia/nemotron-3.5-lightning:free",
    "nvidia/nemotron-3.5-super-vl:free",
    "liquid/lfm-3-1.7b:free",
    "liquid/lfm-2.5-2.6b:free",
    "recursive/flint-v1.0:free",
    "cohere/north-mini-code:free",
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "thinkingmachines/inkling-2-small:free",
    "dots-studio/dots-3-note-preview:free",
]

rows = []
for model in MODELS:
    body = {"model": model, "max_tokens": 400, "temperature": 0.9,
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": USER}]}
    t0 = time.perf_counter()
    try:
        r = httpx.post(URL, json=body, headers=H, timeout=120)
        ms = (time.perf_counter() - t0) * 1000
        if r.status_code != 200:
            print(f"  {model:42s} HTTP {r.status_code} {round(ms):6d}ms  {r.text[:90]}")
            rows.append({"model": model, "status": r.status_code})
            continue
        d = r.json()
        ch = d["choices"][0]
        msg = ch["message"]
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning") or msg.get("reasoning_content") or ""
        usage = d.get("usage", {}) or {}
        rt = (usage.get("completion_tokens_details") or {}).get("reasoning_tokens")
        ok = "OK " if content else "EMPTY"
        print(f"  {model:42s} HTTP 200 {round(ms):6d}ms  {ok:5s} content {len(content):4d}ch "
              f"reasoning {len(reasoning):5d}ch  finish={ch.get('finish_reason')} "
              f"tok={usage.get('completion_tokens')} reasoning_tok={rt}")
        rows.append({"model": model, "status": 200, "ms": round(ms),
                     "content_len": len(content), "reasoning_len": len(reasoning),
                     "finish": ch.get("finish_reason"), "usage": usage,
                     "preview": content[:200], "landed_model": d.get("model")})
    except Exception as exc:
        print(f"  {model:42s} ERR {type(exc).__name__}")
        rows.append({"model": model, "error": type(exc).__name__})

print("\n--- content 非空的模型，其回复前 100 字 ---")
for row in rows:
    if row.get("content_len"):
        print(f"  {row['model']}:")
        print(f"    {row['preview']!r}")

out = os.path.join(os.environ["TEMP"], "openrouter_models_probe.json")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False, indent=2)
print(f"\n落盘: {out}")
