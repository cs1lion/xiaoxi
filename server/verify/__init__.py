"""Verifier（M1 第 5 步，规范 §9.1）。

**确定性规则 + 语义 Judge 分离**：本包只做三种确定性 check
（`file_exists_nonempty` / `syntax_ok` / `csv_assert`），
另外四种（`schema_valid` / `exit_code_zero` / `rubric` / `ask_user`）
明确不做，原因写在 `check.SKIPPED_CHECKS` 里——不是悄悄不实现，是显式标出来。

**为什么单独一个包**（首席架构 2026-10-22 裁定，CONTRACT_V2_SCOPE.md 抬头）：
- 不放 `server/contract/`：那会让契约层「不读文件、不读库、不发网络、不跑命令」
  的边界声明作废；
- 不放 `server/task/`：那会让 `test_task_fsm.py` 的「server/task 不 import
  server.contract」断言失败——Verifier 必须 import 契约层的 `Criterion`/`CheckSpec`。

**`server/task` 调本包不 import 它**，靠注入 callable——数据依赖，不是代码依赖。
本包 **import `server.task` 的纯函数**（`path_in_contract_scope`），
因为那份路径正则在 `lint.py` 与 `task/paths.py` 已经各有一份，**再抄就是第三份**。

边界：**允许读文件**；**禁止**开 socket、连库、subprocess。
这条由 IO 断言机械检查，不是靠自觉。

投影顺序见 `verifier` 模块 docstring——先读 evidence，再 `to_bool_tuple()`。
"""
from __future__ import annotations

from .check import (
    ALL_CHECK_TYPES,
    DETERMINISTIC_EXECUTORS,
    SKIPPED_CHECKS,
    Reader,
    check_csv_assert,
    check_file_exists_nonempty,
    check_syntax_ok,
    default_reader,
)
from .result import (
    KIND_DETERMINISTIC,
    KIND_HUMAN,
    KIND_SEMANTIC,
    KINDS,
    CriterionResult,
    VerificationReport,
)
from .verifier import required_of, verify, verify_one

__all__ = [
    # result
    "CriterionResult", "VerificationReport", "KINDS",
    "KIND_DETERMINISTIC", "KIND_SEMANTIC", "KIND_HUMAN",
    # check
    "ALL_CHECK_TYPES", "DETERMINISTIC_EXECUTORS", "SKIPPED_CHECKS",
    "Reader", "default_reader",
    "check_file_exists_nonempty", "check_syntax_ok", "check_csv_assert",
    # verifier
    "verify", "verify_one", "required_of",
]
