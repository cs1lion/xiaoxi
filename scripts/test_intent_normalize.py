# -*- coding: utf-8 -*-
"""意图归一化单测（B1，issue #42）。

    .\.venv\Scripts\python.exe scripts\test_intent_normalize.py
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.intent_normalize import normalize, normalize_with_trace  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    if condition:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


def main() -> int:
    print("[同一句话的多种礼貌/语气变体归一到同一结果]")
    variants = [
        "帮我整理一下周报",
        "麻烦你整理一下周报",
        "整理一下周报吧",
        "帮我整理一下周报呀",
        "请帮我整理一下周报。",
        "麻烦你帮我整理一下周报吧",
    ]
    results = {normalize(text) for text in variants}
    check("六种变体归一到同一结果", len(results) == 1, f"得到 {results}")

    print("[ASR 同音错字]")
    check("小西 → 小溪", "小溪" in normalize("早上好呀小西"))
    check("晓溪 → 小溪", "小溪" in normalize("晓溪你在吗"))

    print("[只动首尾，不动句中语义]")
    check("句中「帮我」不被去掉",
          "帮我" in normalize("他答应帮我修的，结果忘了"))
    check("句中语气词保留",
          "吧" in normalize("我觉得这样挺好玩的吧大概"))

    print("[可观测：trace 记录每一步]")
    traced = normalize_with_trace("麻烦你帮我整理一下周报吧")
    check("trace 记录了前缀与语气词", len(traced.steps) >= 2, str(traced.steps))
    check("原文被保留", traced.original == "麻烦你帮我整理一下周报吧")
    check("changed 为真", traced.changed is True)

    print("[边界：空与异常输入]")
    check("空串不炸", normalize("") == "")
    check("None 不炸", normalize(None) == "")  # type: ignore[arg-type]
    check("纯标点归一到空", normalize("。。。") == "")

    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
