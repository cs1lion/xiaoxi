# -*- coding: utf-8 -*-
"""worker shim 共享契约：PROTOCOL §22 五事件在各 worker上必须一致的断言。

**为什么抽出来**：四个 worker shim 测试（claude / codex / dsh / workbuddy）
各自驱动 `scripts/worker_shims/` 下不同的 worker，验的是**同一份契约**——
PROTOCOL §22 的五个事件、artifact 必须是真实绝对路径文件、happy path
以 artifact 收尾且不出现 error。这套断言曾被逐字复制四份（plan-107 扫出
31 个跨文件同名断言标签，其中 6 个四文件共有）。

复制四份的真实代价：改契约时漏改一处，**没有任何测试会红**——因为每份
副本在自己文件里都是自洽的。抽成 helper 后契约只有一处定义，四个 worker
都必须过它。

**为什么不合并成一个文件**：那会把「四个 worker 都要过这条契约」这个事实
藏进一个循环里，读者从文件名上看不出有四个 worker。保持四个入口文件，
共享断言走这里。

**边界**：只收四文件**逐字相同**的断言。各 worker 特有的部分（dsh 的
session→step 映射、workbuddy 的 init 进度带模型名、codex 的 reasoning
日志）留在各自文件里——那些不是契约，是各 worker 的实现细节。

Run（由四个 test_*_worker_shim.py 引用，不单独跑）：
    .venv/Scripts/python.exe -B scripts/test_claude_worker_shim.py
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

# PROTOCOL §22 的五个适配器事件。与 server/contract/broadcast 的同名集合
# 保持一致；这里不 import 它，是为了让契约测试不依赖被测方的常量——
# 若两边漂移，应该由测试红来暴露，而不是被同一个来源掩盖。
EVENT_TYPES = {"progress", "log", "token", "artifact", "error"}


def check_contract(check: Callable[..., None], events: list[dict], label: str = "") -> None:
    """对一轮 happy-path 的事件序列断言共享契约。

    `check` 由调用方传入（各文件自己的那个带 passed/failed 累计的 check），
    这样断言失败时计进的是调用方的统计，helper 不自己维护状态。

    `label` 仅用于失败详情里区分是哪个 worker，断言名保持与原先一致——
    门禁报告里的名字不该因为重构而变。
    """
    suffix = f"（{label}）" if label else ""
    types = [str(event.get("type")) for event in events]

    check("all emitted types are protocol events",
          all(kind in EVENT_TYPES for kind in types), f"{types}{suffix}")
    check("progress is emitted", "progress" in types, f"{types}{suffix}")
    check("artifact terminates the happy run",
          bool(types) and types[-1] == "artifact", f"{types}{suffix}")
    check("no error on the happy path", "error" not in types, f"{types}{suffix}")

    artifact = next((e for e in events if e.get("type") == "artifact"), None)
    if artifact is None:
        check("artifact event exists", False, f"no artifact event{suffix}")
        return

    card = artifact.get("artifact") or {}
    refs = [str(item.get("ref")) for item in card.get("artifacts") or []]
    check("artifact card has summary and intercepted",
          bool(card.get("summary")) and bool(card.get("intercepted")),
          f"{card}{suffix}")
    check("artifact ref is one real absolute file",
          len(refs) == 1 and Path(refs[0]).is_absolute() and Path(refs[0]).is_file(),
          f"{refs}{suffix}")
    if refs and Path(refs[0]).is_file():
        check("artifact file content matches",
              Path(refs[0]).read_text(encoding="utf-8") == "OK\n",
              f"{refs}{suffix}")


def check_failure_contract(check: Callable[..., None], events: list[dict],
                           label: str = "") -> None:
    """failure path 的共享契约：以 error 收尾且绝不吐 artifact。"""
    suffix = f"（{label}）" if label else ""
    types = [str(event.get("type")) for event in events]
    check("failure emits the error terminal",
          bool(types) and types[-1] == "error", f"{types}{suffix}")
    check("failure never emits an artifact", "artifact" not in types, f"{types}{suffix}")
    check("failure path also stays inside the protocol",
          all(kind in EVENT_TYPES for kind in types), f"{types}{suffix}")
