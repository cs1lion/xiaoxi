# -*- coding: utf-8 -*-
"""意图分流语料基线：台前快环三层判定的特征化测试（characterization test）。

    .\.venv\Scripts\python.exe scripts\verify_intent_routing_corpus.py

为什么需要它
------------
意图识别改造分两阶段（用户 2026-10-21 立项）：
  阶段一「兜底提示」——疑似意图但正则未命中时给出提示，不静默降级；
  阶段二「模型 shadow」——同一句再跑一次模型判定，只做记录、不参与路由。

两阶段都**不得改变任何现有句子的路由结果**。没有这份基线，"没改变"
只能靠人眼看 diff；有了它，路由结果一漂测试就红，改的人必须显式
承认这次漂移并更新期望值。

与 scripts/verify_world_routing_corpus.py 的分工
------------------------------------------------
那份只管 world 短语扩词（ADR §4.4），正则**逐字转写**进脚本。
本份管三层的整体分流，因此**直接 import 真函数**——转写会在源码迭代后
静默失真，而这里要断言的正是源码当前行为。

断言分组
--------
  A 规范说法  必须稳定命中（回归锁：改造后不许掉）
  B 同义改写  当前未命中；记录为已知缺口（阶段二 shadow 的目标集）
  C 闲聊误报  必须不命中任何一层（误报锁：兜底提示的准入红线）
  D 世界短语  显式点名才进 AgentLoop，裸「世界」不得命中
  E 结构      三层判定仍是纯函数、纯正则；executive_route 仍要求台账双约束

退出码 0 = 全部不变量成立。
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.assistant.service import AssistantService  # noqa: E402
from server.dialogue.orchestrator import (  # noqa: E402
    acceptance_intent, delegation_intent, progress_query_intent,
    telemetry_query_intent,
)

passed, failed, gaps = 0, [], []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


def should_route(text: str) -> bool:
    """真 should_route，非台账相关的依赖用 stub 短路。"""
    stub = AssistantService.__new__(AssistantService)
    stub.note_conversation = SimpleNamespace(should_route=lambda *a, **k: False)
    stub.thread_conversation = SimpleNamespace(should_route=lambda *a, **k: False)
    stub._activity_enabled = lambda: True
    return AssistantService.should_route(stub, text)


# ---------- A 规范说法：必须稳定命中 ----------
A_DELEGATION = [
    "去把这个整理一下", "安排人做个页面", "这件事你盯着，有结论再叫我",
    "派个人去做这个", "把活儿交给小张", "把这个交给他处理",
    "让她去把这份稿子改了", "盯着这个单做完再叫我", "有结论再叫我",
]
A_PROGRESS = ["进度怎么样了", "那单进度怎么样了", "做得怎么样", "交差了没", "做完了吗",
              "有结论了吗", "还在跑吗", "什么时候能好"]
A_TELEMETRY = ["今天 API 烧了多少 token", "战况怎么样", "这月额度还剩多少",
               "接口调用量怎么样", "延迟多少", "花了多少"]
A_ACCEPT = ["收工", "可以了", "就这样", "没问题", "不错", "通过", "这单结了"]
A_REJECT = ["不行，打回重做", "再改一下", "不满意", "重做", "退回"]

# ---------- B 同义改写：当前未命中，已知缺口 ----------
# 阶段一给提示、阶段二做 shadow，二者都不改路由，所以这些必须保持 MISS；
# 哪天命中率上来了，这里会红——那是要显式更新期望的信号，不是 bug。
B_DELEGATION_MISS = [
    "帮我把那份季度报告生成一下", "你找人把服务器日志清一下",
    "我想让谁帮我搞一下那个脚本", "把这个需求落实下去", "这份活儿交出去吧",
    "把那个表格重新做一遍", "你去弄一下这个事情", "帮我跑一下测试",
    "能不能让谁帮我弄弄这个", "这活得有人干一下", "麻烦你那边安排一下",
    "叫个人来把这个收拾了",
]
B_PROGRESS_MISS = [
    "看看那单到哪一步了", "那事儿办得咋样了", "目前进展如何", "这活儿还要多久",
    "现在什么阶段了", "这单什么情况了", "搞到哪儿了", "快好了没",
]

# ---------- C 闲聊误报：必须一层都不命中 ----------
C_CHITCHAT = [
    "今天天气不错啊", "帮我理一理思路", "我有点累", "你说我该不该换工作",
    "就是第二个", "你好", "确认", "下午三点",
    "把第二段改自然一点", "语气轻松一点", "结尾改具体一点",
]

# ---------- D 世界短语：显式点名才路由 ----------
D_WORLD_POSITIVE = ["去像素世界里看看", "像素世界现在什么情况", "小镇里有人吗"]
D_WORLD_NEGATIVE = ["世界杯开始了吗", "你的世界观是什么", "昨晚世界杯看了没"]


def main() -> int:
    print("[A 规范说法·必须命中]")
    for text in A_DELEGATION:
        check(f"delegation {text}", delegation_intent(text))
    for text in A_PROGRESS:
        check(f"progress {text}", progress_query_intent(text))
    for text in A_TELEMETRY:
        check(f"telemetry {text}", telemetry_query_intent(text))
    for text in A_ACCEPT:
        check(f"accept {text}", acceptance_intent(text) == "accept", str(acceptance_intent(text)))
    for text in A_REJECT:
        check(f"reject {text}", acceptance_intent(text) == "reject", str(acceptance_intent(text)))

    print("[B 同义改写·已知缺口，当前必须仍为 MISS]")
    for text in B_DELEGATION_MISS:
        hit = delegation_intent(text)
        gaps.append(("delegation", text, hit))
        check(f"delegation gap {text}", not hit)
    for text in B_PROGRESS_MISS:
        hit = progress_query_intent(text)
        gaps.append(("progress", text, hit))
        check(f"progress gap {text}", not hit)
    # 缺口集也必须落普通对话——否则"未命中快环"却进了 AgentLoop，那是另一回事
    for text in B_DELEGATION_MISS + B_PROGRESS_MISS:
        check(f"gap 落普通对话 {text}", not should_route(text))

    print("[C 闲聊·误报锁]")
    for text in C_CHITCHAT:
        layer = (
            "delegation" if delegation_intent(text) else
            "progress" if progress_query_intent(text) else
            "telemetry" if telemetry_query_intent(text) else
            "accept/reject" if acceptance_intent(text) else
            "should_route" if should_route(text) else None
        )
        check(f"闲聊不命中 {text}", layer is None, f"误命中 {layer}")

    print("[D 世界短语·边界]")
    for text in D_WORLD_POSITIVE:
        check(f"世界正例进 AgentLoop {text}", should_route(text))
    for text in D_WORLD_NEGATIVE:
        # 裸「世界」不得触发路由（世界杯/世界观是日常词）
        layer = ("delegation" if delegation_intent(text) else
                 "progress" if progress_query_intent(text) else
                 "should_route" if should_route(text) else None)
        check(f"世界反例不路由 {text}", layer is None, f"误命中 {layer}")

    print("[E 结构·三层仍是纯函数]")
    for fn in (delegation_intent, progress_query_intent, telemetry_query_intent,
               acceptance_intent):
        try:
            src = inspect.getsource(fn)
        except OSError:  # pragma: no cover - 单文件脚本总有源码
            src = ""
        check(f"{fn.__name__} 是纯函数（无 await/无网络）",
              "await" not in src and "requests" not in src, fn.__name__)
        check(f"{fn.__name__} 对非字符串返回假",
              fn(None) in (False, None) and fn("") in (False, None), fn.__name__)
    # executive_route 的设计不变量：意图本身永不路由，必须叠真实台账状态
    route_src = inspect.getsource(
        __import__("server.dialogue.orchestrator", fromlist=["orchestrator"])
        .Orchestrator.executive_route)
    check("executive_route 保留台账双约束",
          "pending_reports" in route_src and "open_commissions" in route_src)
    check("executive_route 仍有 executive is None 短路",
          "executive is None" in route_src)

    print()
    print("=" * 72)
    print(f"已知缺口：delegation {len(B_DELEGATION_MISS)} 条 / progress {len(B_PROGRESS_MISS)} 条")
    print(f"其中已命中的（应全为 0）：{sum(1 for _, _, hit in gaps if hit)}")
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}")
        for item in failed[:20]:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
