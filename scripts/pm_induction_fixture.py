"""内联共享域夹具：替代随 /eval 清理被删、从未入 git 的
eval/research/procedural_memory_eval_cases.json。

为什么必须内联：test_procedural_memory_induction.py 的 PipelineTests 经
target.load_sources()（scripts/eval_procedural_memory_induction.py:50-57）读这个
文件；该文件随 /eval 清理被删、从未入 git，还被
procedural_memory_eval_snapshot.py 的 SOURCE_FILES 收进冻结白名单（:35）。
2026-10-25 曾据「测试文件里没有 read_text」误判它自包含而删掉跳过，结果 5 条
立刻红——夹具依赖在被测模块里，grep 测试文件看不见。

本模块把夹具搬进测试进程：build_sources() 内存构造 → 写临时 JSON →
monkeypatch target.load_sources 指向它。与 T3b 给 test_memory_eval.py 做的
内联化同一套路。

夹具形状由 load_sources 的校验钉死（eval_procedural_memory_induction.py:52-55）：
  3 个 source_bundle、id 互异、每个恰好 2 条 recipe。
每条 recipe 的字段白名单与执行语义由 SourceCorpus.prepare() /
_check_recipe() 钉死（procedural_memory_eval_domain.py:427-568）：
  必填 id / user_goal / note_fixture / environment_controller_stub / host_only_role；
  可选 host_steps / controlled_assistant_message / concurrent_append_literal /
        host_capabilities / host_confirmation_policy / host_attempt_confirm_after_rejection；
  白名单外字段直接 ValueError('unexpected_source_recipe_field')。
environment_controller_stub 的 action 必须是 'edit'，否则
ValueError('unsupported_source_controller_stub')（domain.py:444-445）。

2026-10-26 重建说明：本文件曾因并行会话的提交/清扫从磁盘丢失，导致
test_procedural_memory_induction.py 的 setUpModule ImportError。按上述约束原样
重建，并用该测试本身验证（8 条全绿即形状正确）。
"""
from __future__ import annotations

import atexit
import json
import shutil
import tempfile
from pathlib import Path

import pm_eval_fixture

# install() 替换掉的原 load_sources，**按 target 模块**存（id(module) 作键），
# 供 restore() 还原。S6 对齐 T3b 模板；R9 从单一变量改成 dict——单一变量在
# 多 target 时会串台：install(A)→install(B)→restore(b) 会把 B 安上 A 的原函数，
# 再 restore(a) 变 no-op，A 永久带 patch。
#
# id(module) 作键的前提（照抄前先知道）：模块只要还在 sys.modules 里，id 就
# 稳定；且 restore() 用 pop、不留陈旧条目。会破的唯一路径是「install 了却从不
# restore」叠加「del sys.modules[...] 后重新 import 且复用同一地址」——当前唯一
# 调用方（tearDownModule）不会走到那儿。要更稳可改用模块对象本身作键。
_ORIGINAL_LOAD_SOURCES: dict[int, object] = {}


def build_sources() -> dict:
    """内存构造 3 bundle × 2 recipe 的共享域夹具。

    payload 与 ``pm_eval_fixture.build_sources()`` 同源：多个测试进程会读写
    ``eval/research/procedural_memory_eval_cases.json``，各写各的会导致
    子进程夹具与内存夹具数据分裂，因此这里只做转发。
    """
    import pm_eval_fixture
    return pm_eval_fixture.build_sources()


def install(target_module) -> Path:
    """让 target_module.load_sources 返回内联夹具，并落一份到子进程能读的路径。

    返回值（调用方无需关心，atexit 会清）：磁盘 sentinel 已存在时返回
    `tempfile.mkdtemp()` 里那个同名文件；本进程新写出 sentinel 时返回仓库内
    那份的路径。两者都由 atexit 清理。

    两份落盘，各有其原因：
    1. `tempfile` 里的副本——本进程 monkeypatch 的数据源，语义等价于原实现
       读一遍 JSON（原实现返回 deepcopy，这里用 json 往返达到同样效果）。
    2. `eval/research/procedural_memory_eval_cases.json`——**子进程**要读的
       真实路径。`test_real_frozen_prepare_*` 用 subprocess 跑
       `--prepare-sources`，新解释器里没有 monkeypatch，只能从磁盘读；而
       procedural_memory_eval_snapshot.py 的 SOURCE_FILES 白名单
       （:22-36）把该路径列为快照必需文件，缺失即 SnapshotError。

    为什么不能靠「改模块级路径常量 + 调原函数」：load_sources() 把路径硬编码
    在函数体里（eval_procedural_memory_induction.py:51），模块级只有 ROOT、
    没有可替换的 CASE_PATH。

    清理为什么重要：该路径被 .gitignore:58（/eval/）覆盖、不入库，而
    test_procedural_memory_eval.py 的 EVAL_FIXTURES_AVAILABLE（:30-31）要求
    cases 与 oracles 同时存在才真跑。2026-10-26 曾手动补了这个文件忘删，旧判定
    只看 cases，结果那 20 条改成真跑、因还缺 oracles 而 19 errors + 1 failure；
    2026-11-08 已把判定改成双文件，install() 仍必须注册 atexit 清理，绝不让夹具
    漏到别的测试进程。

    **数据分裂（必须知道）**：PipelineTests 用内存内联夹具，而
    test_real_frozen_prepare_* 走 subprocess 读**磁盘上那份**。磁盘那份只在
    原本不存在时才写，且绝不覆盖使用者预先放好的文件——覆盖会毁用户数据，
    所以「总是覆盖」不是选项。后果是：使用者在 eval/research/ 放了自己的
    procedural_memory_eval_cases.json 时，两类测试读的不是同一份数据，套件
    可能因与被测代码无关的原因变红（实测预置 0-bundle 哨兵 → exit 1）。
    检测到已存在文件时会打印提示，说明子进程实际读的是哪份。

    **已知限制（R9 补记，R13 按实测修正表述）**：子进程夹具写在**共享的**
    gitignored 路径上，而 `test_procedural_memory_eval.py` 的
    `EVAL_FIXTURES_AVAILABLE` 是 **import 时求值**的模块级常量（:30-31）。
    2026-11-08 起它要求 cases 与 oracles **同时**存在：所以 sed 出 sentinel
    只会让 eval 读到 cases、仍因缺 oracles 而诚实跳过，不会再把 20 条改成
    19 errors + 1 failure。遗留风险只剩「使用者同时预置 cases+oracles」时，
    子进程与内存 payload 不同（见上一条）。

    标准入口（`run_gates` 独立子进程、`ci_test_shard.py --workers 4` 并发分片、
    同进程 `unittest discover`）都实测通过；并发写同一份 payload 由
    `pm_eval_fixture` 的文件锁 + lease 串行化。
    """
    sources = build_sources()
    payload = json.dumps(sources, ensure_ascii=False)

    directory = tempfile.mkdtemp(prefix="neurosama-pm-induction-fixture-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    fixture = Path(directory) / "procedural_memory_eval_cases.json"
    fixture.write_text(payload, encoding="utf-8")

    # 子进程读的固定路径。走 pm_eval_fixture 的锁 + lease：只在不存在时写，
    # 只由创建者在最后一个 lease 退出后清理——不覆盖、不误删使用者预置的文件，
    # 也不会在 ci_test_shard --workers 4 的并发进程里被别人提前删掉。
    subprocess_fixture = pm_eval_fixture.CASE_PATH
    available = pm_eval_fixture.ensure_file(
        subprocess_fixture, sources, 'cases-induction', pm_eval_fixture.cases_valid)

    def load_sources():
        # 深拷贝，避免调用方改到共享数据（与原实现返回 deepcopy 同口径）。
        return json.loads(payload)

    # 按 target 存原函数：多 target 时 restore 各自还原，不会把 B 安上 A 的
    # 原函数。setdefault 保住**最初**那个，第二次 install 不覆盖。
    _ORIGINAL_LOAD_SOURCES.setdefault(id(target_module), target_module.load_sources)
    target_module.load_sources = load_sources
    return subprocess_fixture if available else fixture


def restore(target_module) -> None:
    """还原 install() 替换掉的 load_sources（同进程合跑其它模块前必须调）。

    与 T3b 给 scripts/test_memory_eval.py 的 tearDownModule 同款：只在
    setUpModule 打补丁而不还原，同进程里后跑的模块会读到内联夹具。泄漏到
    eval_procedural_memory_diagnostics.py 的 lesson_sources() 时报的是
    StopIteration 而非干净错误码，排障会找错方向。

    按 target 还原：install() 为每个 target 各存一份原函数，这里只还原调用方
    指定的那个，不影响其它 target。
    """
    original = _ORIGINAL_LOAD_SOURCES.pop(id(target_module), None)
    if original is not None:
        target_module.load_sources = original
