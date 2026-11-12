# -*- coding: utf-8 -*-
"""讯飞星火（OpenAI 兼容）端点与项目 llm.py 契约的逐字段对齐。

项目 llm.py:145 POST {base_url}/chat/completions，body 字段：
model / messages / temperature / max_tokens /
[frequency_penalty] / [presence_penalty] / [response_format={"type":"json_object"}] /
[reasoning_effort]

逐个测这些字段在讯飞端点上是否被接受，以及默认是否开思考模式。
"""
from __future__ import annotations

import json
import os
import sys
import time

import httpx

sys.stdout.reconfigure(encoding="utf-8")

BASE = os.environ.get("XF_BASE", "https://maas-api.cn-huabei-1.xf-yun.com/v1")
KEY = os.environ.get("XF_KEY", "")
MODEL = "spark-x2.6"
URL = f"{BASE}/chat/completions"
H = {"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"}

RESULTS = []


def call(body, *, label, timeout=120):
    t0 = time.perf_counter()
    r = httpx.post(URL, json=body, headers=H, timeout=timeout)
    ms = (time.perf_counter() - t0) * 1000
    ok = r.status_code == 200
    detail = ""
    if ok:
        d = r.json()
        ch = d["choices"][0]
        msg = ch["message"]
        content = msg.get("content") or ""
        reasoning = msg.get("reasoning_content") or ""
        detail = (f"content={len(content)}ch reasoning={len(reasoning)}ch "
                  f"finish={ch.get('finish_reason')} "
                  f"usage={d.get('usage', {}).get('completion_tokens')}tok")
    else:
        detail = r.text[:160]
    RESULTS.append({"label": label, "status": r.status_code, "ms": round(ms), "detail": detail})
    print(f"  {label:38s} HTTP {r.status_code}  {round(ms):6d}ms  {detail}")
    return r


def main() -> int:
    print("[1] 基线（项目默认形状）")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400}, label="model/messages/temp/max_tokens")

    print("\n[2] 项目可选字段逐一试探")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400, "frequency_penalty": 0.2},
         label="+frequency_penalty=0.2")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400, "presence_penalty": 0.2},
         label="+presence_penalty=0.2")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400, "reasoning_effort": "low"},
         label="+reasoning_effort=low")
    call({"model": MODEL, "messages": [{"role": "user", "content": "只回JSON {\"a\":1}"}],
          "temperature": 0.0, "max_tokens": 400,
          "response_format": {"type": "json_object"}},
         label="+response_format=json_object")

    print("\n[3] 思考模式开关")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400, "enable_thinking": False},
         label="+enable_thinking=false")
    call({"model": MODEL, "messages": [{"role": "user", "content": "你好"}],
          "temperature": 0.9, "max_tokens": 400, "enable_thinking": True},
         label="+enable_thinking=true")

    print("\n[4] 抽取用的 JSON 契约（项目 EXTRACT_SYSTEM 要 memories 结构）")
    call({"model": MODEL, "temperature": 0.0, "max_tokens": 600,
          "messages": [{"role": "system", "content":
                        '返回 JSON：{"memories":[{"kind":"fact","subject":"user","key":"k","value":"v"}]}'},
                       {"role": "user", "content": "我说我养了一只叫年糕的猫"}],
          "response_format": {"type": "json_object"}, "enable_thinking": False},
         label="extraction-shaped json")

    print("\n[5] 错误路径")
    call({"model": "no-such-model", "messages": [{"role": "user", "content": "x"}],
          "max_tokens": 10}, label="bad model name")
    r = httpx.post(URL, json={"model": MODEL, "messages": [{"role": "user", "content": "x"}],
                              "max_tokens": 10}, headers={"Content-Type": "application/json"}, timeout=60)
    print(f"  {'no auth header':38s} HTTP {r.status_code}  {r.text[:100]}")

    out = os.path.join(os.environ["TEMP"], "xfyun_contract.json")
    with open(out, "w", encoding="utf-8") as fh:
        json.dump(RESULTS, fh, ensure_ascii=False, indent=2)
    print(f"\n结果落盘: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
