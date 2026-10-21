# -*- coding: utf-8 -*-
"""意图怀疑层与约束注入的单测（离线，无需 API Key）。

    .\.venv\Scripts\python.exe scripts\test_intent_guard.py

覆盖四类：
  A 召回——同义改写的委托/进度必须被怀疑层抓到
  B 误报——闲聊必须一条都不抓
  C 注入——dynamic_prompt 空串分支逐字节不变；非空时约束在末尾
  D 纯函数——guard_for 无状态、不抛异常（limit_context 会反复调用它）
  E 第二条静默路径——_DELEGATION 命中但 commission_accept 样本池为空
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.dialogue.intent_guard import (  # noqa: E402
    DELEGATION_GUARD, PROGRESS_GUARD, SUSPECT_DELEGATION, SUSPECT_PROGRESS,
    classify, guard_for,
)
from server.persona import dynamic_prompt  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# A 派单同义改写：这是本改动存在的理由，召回必须高
A_DELEGATION = [
    "帮我把那份季度报告生成一下", "你找人把服务器日志清一下",
    "我想让谁帮我搞一下那个脚本", "把这个需求落实下去", "这份活儿交出去吧",
    "把那个表格重新做一遍", "帮我跑一下测试", "能不能让谁帮我弄弄这个",
    "这活得有人干一下", "麻烦你那边安排一下", "叫个人来把这个收拾了",
]
# 已知漏网：「你去弄一下这个事情」—— 保留在语料里，避免以后被悄悄"修绿"
A_DELEGATION_KNOWN_MISS = ["你去弄一下这个事情"]

A_PROGRESS = [
    "看看那单到哪一步了", "那事儿办得咋样了", "目前进展如何", "这活儿还要多久",
    "现在什么阶段了", "这单什么情况了", "搞到哪儿了", "快好了没",
]

# B 闲聊：不能因为一句无关的叮嘱就把对话带偏
B_CHITCHAT = [
    "今天天气不错啊", "帮我理一理思路", "我有点累", "你说我该不该换工作",
    "就是第二个", "你好", "确认", "下午三点",
    "语气轻松一点", "结尾改具体一点",
]
# 「把第二段改自然一点」会命中怀疑层，但在真实链路上先被 should_route 的
# 笔记路径接走（app.py:779 走 AgentLoop 并 return），stream_respond 不执行，
# 约束不会被注入。单独记录，验证「误报不生效」而不是假装它不存在。
B_CHITCHAT_HANDLED_EARLIER = ["把第二段改自然一点"]


def main() -> int:
    print("[A 召回·派单同义改写]")
    for text in A_DELEGATION:
        check(f"派单 {text}", classify(text) == SUSPECT_DELEGATION, repr(classify(text)))
    for text in A_DELEGATION_KNOWN_MISS:
        # 断言语义：这条目前是漏的。若将来补上了，这行会红——那是要显式更新语料的信号。
        check(f"已知漏网（当前应为空） {text}", classify(text) == "", repr(classify(text)))

    print("[A 召回·进度同义改写]")
    for text in A_PROGRESS:
        check(f"进度 {text}", classify(text) == SUSPECT_PROGRESS, repr(classify(text)))

    print("[A 优先级·进度先于派单]")
    # 进度句也常含「把/帮我」，若不先判进度会给出错误的约束
    for text in ["帮我把那单的进度报一下", "把进度说一下"]:
        check(f"进度优先 {text}", classify(text) == SUSPECT_PROGRESS, repr(classify(text)))

    print("[B 误报锁·闲聊]")
    for text in B_CHITCHAT:
        check(f"闲聊不触发 {text}", classify(text) == "", repr(classify(text)))
    for text in B_CHITCHAT_HANDLED_EARLIER:
        # 命中是预期的，但它在真实链路上走不到注入点（见文件头说明）
        check(f"误报但走不到注入点 {text}", classify(text) == SUSPECT_DELEGATION,
              repr(classify(text)))

    print("[C 注入·向后兼容]")
    baseline = dynamic_prompt(memory_found=False, current_state="平静")
    check("不传 intent_guard 与传空串逐字节相同",
          dynamic_prompt(memory_found=False, current_state="平静", intent_guard="") == baseline)
    guarded = dynamic_prompt(memory_found=False, current_state="平静",
                             intent_guard=guard_for("帮我把那份季度报告生成一下"))
    check("传约束时输出变化", guarded != baseline)
    check("约束在末尾（不进前缀缓存段）", guarded.endswith(DELEGATION_GUARD))
    check("约束只出现一次", guarded.count(DELEGATION_GUARD) == 1)
    progress_guarded = dynamic_prompt(memory_found=False, current_state="平静",
                                      intent_guard=guard_for("那事儿办得咋样了"))
    check("进度约束在末尾", progress_guarded.endswith(PROGRESS_GUARD))

    print("[D 纯函数·可被 limit_context 反复调用]")
    calls = [guard_for("帮我把这个弄一下") for _ in range(50)]
    check("重复调用结果恒定", len(set(calls)) == 1, str(len(set(calls))))
    check("无状态：与调用顺序无关",
          guard_for("帮我把这个弄一下") == DELEGATION_GUARD
          and guard_for("今天天气不错") == ""
          and guard_for("帮我把这个弄一下") == DELEGATION_GUARD)
    for bad in (None, "", "   ", 123, ["a"], {"a": 1}, object()):
        try:
            result = (classify(bad), guard_for(bad))
            check(f"非字符串不抛异常 {type(bad).__name__}", result == ("", ""), repr(result))
        except Exception as exc:  # noqa: BLE001 - the point is that this never runs
            check(f"非字符串不抛异常 {type(bad).__name__}", False, f"raised {exc!r}")

    print("[E 第二条静默路径·_DELEGATION 命中但样本池为空]")
    # orchestrator.executive_route:1480-1483 命中 delegation_intent 后，
    # 若 commission_accept_line() 返回空串则 return None 落普通对话。
    # 此时路由没建单，但模型仍被 persona 命令说「接单了」——照样虚报。
    # 这条路径的句子必然命中 _DELEGATION，因此也必须命中怀疑层。
    from server.dialogue.orchestrator import delegation_intent
    SILENT_PATH = ["去把这个整理一下", "安排人做个页面", "这件事你盯着，有结论再叫我"]
    for text in SILENT_PATH:
        check(f"命中 _DELEGATION（样本池空的前提） {text}", delegation_intent(text))
        check(f"同时命中怀疑层（约束会注入） {text}", classify(text) == SUSPECT_DELEGATION,
              repr(classify(text)))

    print()
    print("=" * 72)
    if failed:
        print(f"FAILED {len(failed)}/{passed + len(failed)}")
        for item in failed:
            print("  - " + item)
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
