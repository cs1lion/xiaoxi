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

ROOT = Path(__file__).resolve().parents[1]

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

# 本进程自己写过的子进程夹具路径。用来区分「自己刚写的同一份 payload」与
# 「使用者预置的」——R9：否则同进程第二次 install 会把自己写的说成别人预置的，
# 误报数据分裂。
_SELF_WRITTEN_FIXTURES: set[str] = set()

# body 刻意长且在同 bundle 内共享：project_source 只在投影真的更短时才选
# 'reversible_content_tables'（eval_procedural_memory_induction.py:131
# use_compact = compact_chars < raw_chars），而 project_public_bundle 的压缩
# 靠相同 note body 去重成内容表。body 太短或互不相同都压不动，layout 会退回
# 'original_public_processes'，test_actual_candidate_save_reopen... 的
# source_projections['source-local']['layout'] 断言就红。
_LONG_BODY = (
    "本周确定了三件事：接口冻结、文档补全、回归执行。"
    "接口冻结意味着前端可以开始联调，不再等待后端改动；"
    "文档补全需要覆盖新增的两个错误码和重试语义；"
    "回归执行要包含昨晚修复的那条打断链路，并且出报告。"
    "以上四条按优先级排序，接口冻结最优先，回归执行可以放到最后。"
    "如果时间紧张，文档补全可以拆成两次提交，但接口冻结不能拖。"
) * 6

# 替换目标必须在 body 里**唯一**：_check_recipe 的 else 分支（独立精确构造）
# 要求 preview.ok is True，而 preview 对出现多次的 old_text 会返回
# unresolved_replacement / ambiguous_match_rejected，于是 materialized=False
# → source_recipe_materialization_failed。
# _LONG_BODY 重复 6 遍，任何取自其中的片段都会出现 6 的倍数次；所以在末尾
# **追加一段只出现一次的文本**作为 old_text，从构造上保证唯一。
_UNIQUE_ANCHOR = "本轮唯一待办：把回归报告归档到共享目录。"

_LONG_BODY = _LONG_BODY + "\n" + _UNIQUE_ANCHOR

_RECIPE_TEMPLATE = {
    "id": "recipe-placeholder",
    "user_goal": "把会议纪要里的待办补全",
    "note_fixture": {
        "title": "周会纪要",
        "body": _LONG_BODY,
        "body_chars": len(_LONG_BODY),
        "body_truncated": False,
        "current_version": 1,
        "selected_version_is_current": True,
    },
    "environment_controller_stub": {
        "action": "edit",
        "replacements": [
            {"old_text": _UNIQUE_ANCHOR,
             "new_text": "本轮唯一待办：把回归报告归档到共享目录并通知QA。"}
        ],
    },
    "host_only_role": "source_environment_controller",
    # 必须显式给 confirmation 策略：_issue_confirmation()
    # （domain.py:265-274）在 host_fixture.confirmation == 'none' 时直接 return、
    # 不发确认事件，prepare() 就拿不到 confirmations、不会 execute('confirm')，
    # 于是 _check_recipe 的 else 分支（独立精确构造）要求 confirmation.ok is True
    # 必然失败 → source_recipe_materialization_failed → public[source_id] 为空 →
    # ScriptedProvider 的 messages[1] IndexError。默认值是 'none'，不设就整条断。
    "host_confirmation_policy": "fresh event after actual preview",
    "controlled_assistant_message": "我已经把待办那行改成了回归执行并出报告。",
}


def build_sources() -> dict:
    """内存构造 3 bundle × 2 recipe 的共享域夹具。

    id 互异、每 bundle 恰好 2 条 recipe——load_sources 的硬校验。
    recipe 内容刻意同构：H4 的目标是「让 PipelineTests 真跑起来、消灭整组
    假绿」，不是测 domain 的语义分支（那属于能力探针，另有所属）。
    """
    bundles = []
    for bundle_index in range(3):
        # 同一 bundle 内的 recipe 共享同一个 note_fixture：投影时相同 body 会
        # 去重成内容表，compact_chars < raw_chars 才成立，layout 才是
        # reversible_content_tables。user_goal 仍逐条不同（公开过程要可区分）。
        recipes = []
        for recipe_index in range(2):
            recipe = json.loads(json.dumps(_RECIPE_TEMPLATE))
            recipe["id"] = f"bundle{bundle_index}-recipe{recipe_index}"
            recipe["user_goal"] = f"把第 {bundle_index} 组纪要的待办补全（{recipe_index}）"
            recipes.append(recipe)
        # 第一个 bundle 的 id 必须是 'source-local'：execute_induction 用
        # record['source_bundle_id']（= b['id']）作 report['source_projections']
        # 的键，而 test_actual_candidate_save_reopen... 断言
        # report['source_projections']['source-local']['layout']。
        bundle_id = "source-local" if bundle_index == 0 else f"source-bundle-{bundle_index}"
        bundles.append({
            "id": bundle_id,
            "recipes": recipes,
        })
    return {"source_bundles": bundles}


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

    清理为什么重要：该路径被 .gitignore:54（/eval/）覆盖、不入库，但
    test_procedural_memory_eval.py 的 EVAL_CASES_AVAILABLE 正是探测它。
    2026-10-26 曾手动补了这个文件忘删，结果那 20 条本应跳过的测试改成真跑，
    因还缺 oracles 而 19 errors + 1 failure。所以 install() 必须自己注册
    atexit 清理，绝不让夹具漏到别的测试进程。

    **数据分裂（必须知道）**：PipelineTests 用内存内联夹具，而
    test_real_frozen_prepare_* 走 subprocess 读**磁盘上那份**。磁盘那份只在
    原本不存在时才写，且绝不覆盖使用者预先放好的文件——覆盖会毁用户数据，
    所以「总是覆盖」不是选项。后果是：使用者在 eval/research/ 放了自己的
    procedural_memory_eval_cases.json 时，两类测试读的不是同一份数据，套件
    可能因与被测代码无关的原因变红（实测预置 0-bundle 哨兵 → exit 1）。
    检测到已存在文件时会打印提示，说明子进程实际读的是哪份。

    **已知限制（R9 补记，R13 按实测修正表述）**：子进程夹具写在**共享的**
    gitignored 路径上，而 `test_procedural_memory_eval.py` 的
    `EVAL_CASES_AVAILABLE` 是 **import 时求值**的模块级常量（:29-30）。所以
    准确的触发条件是「**任何在 sentinel 出现之后才 import
    `test_procedural_memory_eval` 的进程**都会读到它，从而把 20 条本应跳过的
    测试真跑，因还缺 oracles 而 19 errors + 1 failure」。并发跑两个套件只是
    其中一种；顺序合跑同样中招——同进程先跑 induction 写出 sentinel、再 import
    eval 套件，实测即红。

    已实测**安全**的标准入口（共同点是在 sentinel 出现之前就 import 完所有
    模块）：`python -B -m unittest induction.py eval.py` 两种顺序、
    `unittest discover -s scripts -p 'test_procedural_memory_*.py'`、
    `ci.yaml` 的顺序 for 循环、`run_gates` 的独立子进程。会踩到的是懒加载型
    in-process harness。所以别在同一工作副本里边跑 induction 边 import eval 套件。
    """
    sources = build_sources()
    payload = json.dumps(sources, ensure_ascii=False)

    directory = tempfile.mkdtemp(prefix="neurosama-pm-induction-fixture-")
    atexit.register(shutil.rmtree, directory, ignore_errors=True)
    fixture = Path(directory) / "procedural_memory_eval_cases.json"
    fixture.write_text(payload, encoding="utf-8")

    # 子进程读的固定路径。只在原本不存在时才写，且只清理自己写的那份——
    # 不覆盖、不删除使用者预先放好的文件。
    subprocess_fixture = ROOT / "eval" / "research" / "procedural_memory_eval_cases.json"
    created = False
    if not subprocess_fixture.exists():
        subprocess_fixture.parent.mkdir(parents=True, exist_ok=True)
        subprocess_fixture.write_text(payload, encoding="utf-8")
        created = True
        _SELF_WRITTEN_FIXTURES.add(str(subprocess_fixture))

        def _cleanup():
            try:
                subprocess_fixture.unlink()
                _SELF_WRITTEN_FIXTURES.discard(str(subprocess_fixture))
            except OSError:
                pass

        atexit.register(_cleanup)
    elif str(subprocess_fixture) not in _SELF_WRITTEN_FIXTURES:
        # 数据分裂提示：子进程读的是使用者那份，不是上面的内联夹具。
        # 不覆盖是故意的（会毁用户数据），但必须让人知道读的是哪份。
        # 只有**别人**预置的才提示：自己刚写的同一份 payload 不分裂，提示它
        # 只会制造噪音（R9：同进程第二次 install 曾把自己写的说成别人预置的）。
        print(f"[pm_induction_fixture] 注意：{subprocess_fixture} 已存在，未覆盖；"
              "test_real_frozen_prepare_* 的子进程读的是该文件而非内联夹具，"
              "两类测试可能读到不同数据。套件红得莫名其妙时先查这份。")

    def load_sources():
        # 深拷贝，避免调用方改到共享数据（与原实现返回 deepcopy 同口径）。
        return json.loads(payload)

    # 按 target 存原函数：多 target 时 restore 各自还原，不会把 B 安上 A 的
    # 原函数。setdefault 保住**最初**那个，第二次 install 不覆盖。
    _ORIGINAL_LOAD_SOURCES.setdefault(id(target_module), target_module.load_sources)
    target_module.load_sources = load_sources
    return subprocess_fixture if created else fixture


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
