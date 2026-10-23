# -*- coding: utf-8 -*-
"""第 9 步接线的不变量测试：三组语料的**分支落点**必须符合预期。

    .\.venv\Scripts\python.exe scripts\test_intent_gate_wiring.py

为什么需要它
------------
规范 `:1222` 要求「IntentGate 必须也命中现在正则能命中的全部，不能接上去
反而少认一批」。这条不能靠人眼看 diff——要写成断言，一漂就红。

**关键：「不倒退」的判据是分支落点，不是最终结果。**

| 句子 | 现在 | 改造后允许 | 改造后不允许 |
|---|---|---|---|
| A 组 9 句 | 进 delegation 分支 | 进 delegation 分支，可能转成 clarify_task | 静默落普通对话 |
| B 组 12 句 | 静默落普通对话 | 进 delegation 分支（这是升级） | 静默落普通对话（没升级） |
| C 组 11 句 | 落普通对话 | 仍落普通对话 | 进任何建单分支 |

改造后 A 组可能从「建单」变成「追问」，那是**正确的新行为**，不是倒退。
所以判据不能写成「必须建单」。

当前状态
--------
本文件在接线**之前**跑，B 组那条会红——这是**故意的**：
红了才证明这条断言有牙。接完线它应当变绿。
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.dialogue.orchestrator import Orchestrator  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(f"{name}{'  ' + detail if detail else ''}")
        print(f"  FAIL {name}  {detail}")


# 与 scripts/verify_intent_routing_corpus.py 逐字一致
A_DELEGATION = [
    "去把这个整理一下", "安排人做个页面", "这件事你盯着，有结论再叫我",
    "派个人去做这个", "把活儿交给小张", "把这个交给他处理",
    "让她去把这份稿子改了", "盯着这个单做完再叫我", "有结论再叫我",
]
B_DELEGATION = [
    "帮我把那份季度报告生成一下", "你找人把服务器日志清一下",
    "我想让谁帮我搞一下那个脚本", "把这个需求落实下去", "这份活儿交出去吧",
    "把那个表格重新做一遍", "你去弄一下这个事情", "帮我跑一下测试",
    "能不能让谁帮我弄弄这个", "这活得有人干一下", "麻烦你那边安排一下",
    "叫个人来把这个收拾了",
]
C_CHITCHAT = [
    "今天天气不错啊", "帮我理一理思路", "我有点累", "你说我该不该换工作",
    "就是第二个", "你好", "确认", "下午三点",
    "把第二段改自然一点", "语气轻松一点", "结尾改具体一点",
]

# 「进建单分支」的 kind 集合。改造后 delegation 可能变成 clarify_task。
DELEGATION_KINDS = ("delegation", "clarify_task", "task_rejected")

# 接线是否已发生。判据是**可执行的**，不是人手工改的开关：
# `app.py` 里出现 clarify_task 分派，就说明接线已落。
#
# 为什么需要这个开关（探针逼出来的）：接线前 A 组 9 句全是 "delegation"，
# 所以把允许集从三个收窄成一个，测试**照样绿**——那种断言答不上
# 「什么改动会让它红」，等于没有。用这个开关把判据分两段：
#   未接线：A 组必须是 delegation（收窄 -> 变红，能测）
#   已接线：A 组允许三者之一（放宽 -> 才需要）
def _wired() -> bool:
    app_source = (ROOT / "server" / "app.py").read_text(encoding="utf-8")
    return "clarify_task" in app_source


def _allowed_kinds() -> tuple[str, ...]:
    return DELEGATION_KINDS if _wired() else ("delegation",)


def make_orchestrator(has_ledger: bool = False):
    """构造一个能跑 executive_route 的 Orchestrator，台账用 stub。

    `executive_route` 要求真实台账（pending_reports/open_commissions），
    所以这里给一个空台账的 stub——模拟「手上没有待办单」的常见状态。
    """
    orch = Orchestrator.__new__(Orchestrator)
    orch.state = {}
    return orch


def empty_executive():
    return SimpleNamespace(
        pending_reports=lambda: [],
        open_commissions=lambda: [],
        evaluator=None,
    )


def route_kind(text: str) -> str | None:
    orch = make_orchestrator()
    route = orch.executive_route(text, empty_executive(), memory_enabled=False)
    return None if route is None else str(route.get("kind") or "")


def _dedent(source: str) -> str:
    """inspect.getsource 返回带缩进的源码，ast.parse 前要去掉公共缩进。"""
    import textwrap
    return textwrap.dedent(source)


def test_a_group() -> None:
    stage = "已接线" if _wired() else "未接线"
    allowed = _allowed_kinds()
    print(f"[A 组 9 句：现在能命中的，改造后必须仍进 delegation 分支]（{stage}，允许 {allowed}）")
    # 判据分两层：
    #   底线：不能落普通对话（None 就是倒退）——任何阶段都成立
    #   允许集：未接线时只能是 delegation；接线后允许 clarify_task/task_rejected
    #
    # 为什么要分阶段（探针逼出来的）：接线前 A 组 9 句全是 "delegation"，
    # 所以把允许集从三个收窄成一个，测试照样绿——那种断言答不上
    # 「什么改动会让它红」。分阶段后，收窄允许集在「未接线」阶段会变红，
    # 这条断言才有牙。
    for text in A_DELEGATION:
        kind = route_kind(text)
        check(f"A 不落普通对话 {text}",
              kind is not None,
              f"实际 kind=None（静默落普通对话 = 倒退）")
        check(f"A 落在允许的建单分支 {text}",
              kind in allowed,
              f"实际 kind={kind!r}，允许 {allowed}")


def test_b_group() -> None:
    stage = "已接线" if _wired() else "未接线"
    allowed = _allowed_kinds()
    print(f"[B 组 12 句：现在是缺口，第 9 步后应进 delegation 分支]（{stage}）")
    missed = []
    for text in B_DELEGATION:
        kind = route_kind(text)
        ok = kind in allowed
        check(f"B 应转 delegation {text}", ok, f"实际 kind={kind!r}")
        if not ok:
            missed.append(text)
    if missed:
        print(f"    ↑ 现在有 {len(missed)} 句静默——接线前这是预期的，接线后应归零")


def test_c_group() -> None:
    print("[C 组 11 句：闲聊必须仍落普通对话，一句都不许建单]")
    # C 组用全量集合：无论接线前后，任何建单分支都不许进。
    for text in C_CHITCHAT:
        kind = route_kind(text)
        check(f"C 不建单 {text}", kind not in DELEGATION_KINDS,
              f"误进 {kind!r}")


def test_structure() -> None:
    print("[结构：接线点的存在性]")
    # 锁住「接线点在哪」——若有人把 delegation 分支删了，这里会红。
    #
    # 用 AST 而不是子串：上一版用 "delegation_intent" in source，
    # 探针发现把它改成恒真也照绿——因为那条断言的失败条件太弱。
    # AST 能回答「这个函数体里到底有没有一处 return 与 delegation 判定」。
    import ast
    import inspect
    source = inspect.getsource(Orchestrator.executive_route)
    tree = ast.parse(_dedent(source))
    func = tree.body[0]

    has_delegation = False
    has_return_none = False
    for node in ast.walk(func):
        if isinstance(node, ast.If):
            test_src = ast.dump(node.test)
            if "delegation_intent" in test_src:
                has_delegation = True
        if isinstance(node, ast.Return):
            # `return None` 在 AST 里是 Return(value=Constant(None))，
            # 不是 Return(value=None)。写错这一处会让断言恒假——
            # 上一版就是这么写的，探针抓出来了（基线自己红）。
            value = node.value
            if isinstance(value, ast.Constant) and value.value is None:
                has_return_none = True

    check("executive_route 里有 delegation_intent 判定（AST）",
          has_delegation, "分支不见了")
    check("executive_route 里有裸 return None 兜底（AST）",
          has_return_none, "兜底不见了")


def main() -> int:
    # 允许只跑一组：接线前 B 组必红，会掩盖 A/C 的灵敏度。
    # 单组模式让每组都能被独立验证（探针靠它证明每条断言都有牙）。
    only = ""
    for arg in sys.argv[1:]:
        if arg.startswith("--only="):
            only = arg.split("=", 1)[1].strip()

    runners = {
        "a": test_a_group,
        "b": test_b_group,
        "c": test_c_group,
        "structure": test_structure,
    }
    if only:
        if only not in runners:
            print(f"未知分组 {only!r}，可选：{', '.join(runners)}")
            return 2
        runners[only]()
    else:
        for run in runners.values():
            run()

    print()
    print("=" * 74)
    if failed:
        print(f"FAILED {len(failed)}")
        for item in failed[:25]:
            print("  - " + item)
        if not only or only == "b":
            print()
            print("提示：B 组的失败在接线前是**预期的**（那是还没实现的升级）。")
            print("      接完线后本文件应当全绿——那时它才成为回归锁。")
        return 1
    print(f"PASS {passed}/{passed}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
