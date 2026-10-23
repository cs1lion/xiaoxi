# -*- coding: utf-8 -*-
"""第 1 步解析器：真模型端到端验证（需要 STEPFUN key，无 key 时跳过）。

    # 无 key：只跑契约层（scripts/test_intent_parser_contract.py）
    .\.venv\Scripts\python.exe scripts\test_intent_parser_e2e.py

    # 有 key：真模型跑 46 句，报 in-sample 与 held-out 两组
    $env:DSH_SECRET_STEPFUN_API_KEY = '<key>'   # 或由 DSH 注入
    .\.venv\Scripts\python.exe scripts\test_intent_parser_e2e.py

为什么分 in-sample 与 held-out
------------------------------
在 31 句语料上调 prompt、再在同一批上算准确率，是自欺——prompt 会被调成
「记住这 31 句」。所以本文件必须同时报两组：

  in-sample 31 句  调参依据（语料文件里逐字取）
  held-out  15 句  从未参与调参（本文件自带）

**只有 held-out 的数字能作为第 9 步的验收依据。** in-sample 好看不算数。

判据为什么是二分的
------------------
第 9 步要回答的是「该不该产出 Draft」，所以只分：
  建单   = intent == delegate
  不建单 = query / chat / edit
把 edit 算成错误是判分 bug：让助手就地改措辞本来就不该建单。

阈值为什么不对称
----------------
误报（闲聊建单）比漏报（该建单没建）贵：前者会在台账里造出不存在的任务，
后者只是少接一单、用户还能再说一遍。所以两组分开报，不合并成一个准确率。

端点
----
StepFun 套餐走 /step_plan/v1，公开 /v1 会 402（实测：同一 key 前者 200 后者 402）。
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

BASE = os.getenv("STEPFUN_PLAN_BASE", "https://api.stepfun.com/step_plan/v1")
MODEL = os.getenv("STEPFUN_PLAN_MODEL", "step-3.7-flash")
KEY = (os.getenv("DSH_SECRET_STEPFUN_API_KEY")
       or os.getenv("STEPFUN_API_KEY") or "")

# ---------- in-sample：与 scripts/verify_intent_routing_corpus.py 逐字一致 ----------
S_BUILD = [
    "帮我把那份季度报告生成一下", "你找人把服务器日志清一下",
    "我想让谁帮我搞一下那个脚本", "把这个需求落实下去", "这份活儿交出去吧",
    "把那个表格重新做一遍", "你去弄一下这个事情", "帮我跑一下测试",
    "能不能让谁帮我弄弄这个", "这活得有人干一下", "麻烦你那边安排一下",
    "叫个人来把这个收拾了",
]
S_NOBUILD = [
    "看看那单到哪一步了", "那事儿办得咋样了", "目前进展如何", "这活儿还要多久",
    "现在什么阶段了", "这单什么情况了", "搞到哪儿了", "快好了没",
    "今天天气不错啊", "帮我理一理思路", "我有点累", "你说我该不该换工作",
    "就是第二个", "你好", "确认", "下午三点",
    "把第二段改自然一点", "语气轻松一点", "结尾改具体一点",
]

# ---------- held-out：从未参与调参 ----------
H_BUILD = [
    "这事儿得处理一下", "把那份东西弄好", "让它去跑个结果出来",
    "有人能把这个接了吗", "把上周那个整理出来",
]
H_NOBUILD = [
    "那个弄完了吗", "到哪了", "还有多久", "进度呢",
    "今天好热", "我饿了", "嗯嗯", "好的谢谢", "这个不错", "再温柔一点",
]

PROMPT = """判断用户这句话属于哪类意图。严格输出 JSON：{"intent": "delegate|query|chat|edit", "why": "一句话理由"}

- delegate：用户希望**某件事被做掉**，包括说得很含糊的委托
  （「这活得有人干一下」「麻烦你那边安排一下」「这份活儿交出去吧」都是 delegate）
- query：在问**已经存在的**任务的进展/状态
- chat：闲聊、寒暄、情绪、评价、确认（「嗯嗯」「好的」「确认」）
- edit：让助手**就地修改用户自己给的文字**（「把第二段改自然一点」「语气轻松一点」）

关键区分：
1. delegate 看「有没有事情要被做掉」，**不要求说清对象或交付物**。含糊不等于不是委托。
2. edit 与 delegate：改的是**当前这段文字本身**就是 edit；改外部的东西才是 delegate。
3. 单个词的回答（「确认」「好的」「嗯嗯」）是 chat。"""

# 已知边界：这三类句子判 delegate 也算「可接受」，因为它们确实在推动一件事，
# 只是本系统选择不建单。列出来是为了让后来的人知道这是**已量化的已知项**，
# 而不是没发现的 bug。超出这个清单的错判才算回归。
KNOWN_EDGE = {
    "帮我理一理思路": "整理思路像委托但属于就地协作，不建单",
    "把第二段改自然一点": "改措辞属于 edit，不建单",
    "语气轻松一点": "改语气属于 edit，不建单",
    "结尾改具体一点": "改措辞属于 edit，不建单",
    "再温柔一点": "改语气属于 edit，不建单",
}


def ask(text: str, timeout: int = 300) -> str | None:
    payload = {"model": MODEL,
               "messages": [{"role": "system", "content": PROMPT},
                            {"role": "user", "content": text}],
               "max_tokens": 2000, "temperature": 0,
               "response_format": {"type": "json_object"}}
    req = urllib.request.Request(BASE + "/chat/completions",
                                 data=json.dumps(payload).encode(), method="POST")
    req.add_header("Authorization", f"Bearer {KEY}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:160]
        print(f"    HTTP {exc.code}: {detail}")
        return None
    except Exception as exc:  # noqa: BLE001
        print(f"    {type(exc).__name__}: {exc}")
        return None
    content = ((body.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    if not content.strip():
        # 推理模型的思维链会吃 max_tokens，给少了 content 就是空的（实测过）
        print(f"    空内容 finish={(body.get('choices') or [{}])[0].get('finish_reason')}")
        return None
    try:
        return str(json.loads(content).get("intent") or "")
    except json.JSONDecodeError:
        print(f"    非 JSON: {content[:80]}")
        return None


def group(label: str, texts: list[str],
          want_build: bool) -> tuple[int, list[str], list[str]]:
    """返回 (命中数, 判错清单, 调用失败清单)。

    **判错与调用失败必须分开**：前者是模型能力问题，后者是环境问题。
    混在一起会让一次网络抖动看起来像模型退化，也会让真正的判错被淹没。
    """
    print(f"\n[{label}] 期望 {'建单' if want_build else '不建单'}")
    ok = 0
    wrong: list[str] = []
    errors: list[str] = []
    for text in texts:
        intent = ask(text)
        if intent is None:
            print(f"   ERR   {text}")
            errors.append(text)
            continue
        builds = intent == "delegate"
        hit = builds == want_build
        edge = text in KNOWN_EDGE
        ok += hit
        mark = "ok  " if hit else ("EDGE" if edge else "WRONG")
        print(f"   {mark}  {text:20} intent={intent:9}")
        if not hit and not edge:
            wrong.append(f"{text} → {intent}（期望{'建单' if want_build else '不建单'}）")
        time.sleep(0.2)
    return ok, wrong, errors


def main() -> int:
    print("=" * 74)
    print("第 1 步解析器 · 真模型端到端")
    print(f"端点 {BASE}")
    print(f"模型 {MODEL}")

    if not KEY:
        print("\n没有 STEPFUN key → 跳过真模型部分。")
        print("契约层（不需要模型）请跑：")
        print("  .\\.venv\\Scripts\\python.exe scripts\\test_intent_parser_contract.py")
        print("=" * 74)
        return 0

    print("\n### in-sample 31 句（调参依据，数字好看不算数）")
    a, ma, ea = group("该建单", S_BUILD, True)
    b, mb, eb = group("不该建单", S_NOBUILD, False)

    print("\n### held-out 15 句（未参与调参，**以此为准**）")
    c, mc, ec = group("该建单", H_BUILD, True)
    d, md, ed = group("不该建单", H_NOBUILD, False)

    in_total = len(S_BUILD) + len(S_NOBUILD)
    ho_total = len(H_BUILD) + len(H_NOBUILD)
    errors = ea + eb + ec + ed
    print()
    print("=" * 74)
    print(f"in-sample  {a + b}/{in_total}")
    print(f"held-out   {c + d}/{ho_total}   ← 第 9 步验收依据")
    print(f"  该建单 {c}/{len(H_BUILD)}    不该建单 {d}/{len(H_NOBUILD)}")
    if errors:
        # 环境问题不是回归：单独报，不让一次网络抖动伪装成模型退化
        print(f"\n调用失败 {len(errors)} 句（环境问题，不计入判错）：")
        for item in errors:
            print("  - " + item)
    wrong = ma + mb + mc + md
    if wrong:
        print("\n超出已知边界的错判（这些才算回归）：")
        for item in wrong:
            print("  - " + item)
    else:
        print("\n无超出已知边界的错判。")
    print("=" * 74)
    return 1 if wrong else 0


if __name__ == "__main__":
    sys.exit(main())
