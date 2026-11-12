# -*- coding: utf-8 -*-
"""讯飞星火在项目默认 max_tokens=400 下会不会「思考吃满预算 → content 为空」。

server/llm.py:110-113 明确写过这个失败形态：
「A reasoning model given a tight token budget will spend all of it thinking and
return empty content with finish_reason='length' — which looks identical to
"the model has nothing to say" unless you keep the metadata.」

讯飞 spark-x2.6 默认开思考（reasoning_content 有几百字）。项目 .env 默认
LLM_MAX_TOKENS=400。这条脚本测：思考开着 + 400 预算 + 稍复杂的 prompt，
content 会不会空、finish_reason 会不会是 length。
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

URL = "https://maas-api.cn-huabei-1.xf-yun.com/v1/chat/completions"
H = {"Authorization": f"Bearer {os.environ.get('XF_KEY', '')}",
     "Content-Type": "application/json"}
MODEL = "spark-x2.6"

# 项目真实形态的长 system prompt（persona 片段）+ 用户输入
SYSTEM = (
    "你是小溪，一个有性格的中文语音陪伴搭档。你的语气自然随意，像熟人聊天，"
    "不要播音腔，不刻意表演。你有分档记忆检索的结果，可能包含明确说过、推测、"
    "不确定三档，也可能为空。检索为空时不要用常识脑补共同经历，没查到不等于没发生。"
    "回答控制在两三句话以内，可以带一个轻松的动作或情绪，但不要每次都用。"
)
USER = "我昨天加班到现在才弄完那个破项目，你说我是不是该跳槽了？"

CASES = [
    ("thinking-on, 400 budget", {"max_tokens": 400}, None),
    ("thinking-on, 400, persona prompt", {"max_tokens": 400}, (SYSTEM, USER)),
    ("thinking-off, 400, persona prompt", {"max_tokens": 400, "enable_thinking": False}, (SYSTEM, USER)),
    ("thinking-on, 1200 budget", {"max_tokens": 1200}, (SYSTEM, USER)),
    ("thinking-off, 1200 budget", {"max_tokens": 1200, "enable_thinking": False}, (SYSTEM, USER)),
]

print(f"{'case':40s} {'HTTP':5s} {'ms':>7s} {'content':>8s} {'reason':>7s} {'finish':>8s}")
rows = []
for label, extra, msgs in CASES:
    messages = []
    if msgs:
        messages = [{"role": "system", "content": msgs[0]},
                    {"role": "user", "content": msgs[1]}]
    else:
        messages = [{"role": "user", "content": "你好"}]
    body = {"model": MODEL, "messages": messages,
            "temperature": 0.9, **extra}
    t0 = time.perf_counter()
    r = httpx.post(URL, json=body, headers=H, timeout=180)
    ms = (time.perf_counter() - t0) * 1000
    if r.status_code != 200:
        print(f"{label:40s} {r.status_code:5d} {round(ms):7d}  {r.text[:100]}")
        continue
    d = r.json()
    ch = d["choices"][0]
    msg = ch["message"]
    content = msg.get("content") or ""
    reasoning = msg.get("reasoning_content") or ""
    usage = d.get("usage", {})
    print(f"{label:40s} {r.status_code:5d} {round(ms):7d} {len(content):8d} {len(reasoning):7d} "
          f"{str(ch.get('finish_reason')):>8s}  usage={usage.get('completion_tokens')}")
    rows.append({"label": label, "ms": round(ms), "content_len": len(content),
                 "reasoning_len": len(reasoning), "finish": ch.get("finish_reason"),
                 "usage": usage,
                 "content_preview": content[:120]})

print("\n--- 各 case 的 content 前 80 字 ---")
for row in rows:
    print(f"  {row['label']}:")
    print(f"    {row['content_preview']!r}")

out = os.path.join(os.environ["TEMP"], "xfyun_budget.json")
with open(out, "w", encoding="utf-8") as fh:
    json.dump(rows, fh, ensure_ascii=False, indent=2)
print(f"\n落盘: {out}")
