# -*- coding: utf-8 -*-
"""server/verify 的行为回归 + IO 禁令机械检查（被承诺却长期缺失的那条门）。

出处：`server/verify/check.py:30` 与 `verifier.py:30` 的 docstring 写着
「禁止开 socket、连库、subprocess——这条由 `scripts/test_verify.py` 的 IO 断言
机械检查」，但该文件从未存在于工作树（docs/TESTING.md:201，2026-10-26 核）。
后果不是少一条测试，而是那两条 IO 禁令**没有任何机械强制**——唯一的相邻保障是
`scripts/test_task_fsm.py:1649` 的一条静态 import 限制，它不断言行为。
本文件把承诺补成现实：

1. 三种确定性 check 的正反例（file_exists_nonempty / syntax_ok / csv_assert）；
2. reader 注入：判定不碰真实文件系统，全部走假 reader；
3. IO 静态断言：`server/verify/**.py` 不得出现 socket/连接/子进程原语，
   且不得 import 被禁止的 server 子包；
4. 桥接 `server/assistant/acceptance_verify.py`（M2，issue #61）的渲染契约：
   False → blocker finding、True → 无声、None → 只进 notes 绝不进 findings；
   note 类非文件 ref 不当文件读（MAJOR-1）；无 name 的 artifact_json 跳过。

只用临时目录与假 reader；零网络、零模型、零台账写入。
Run: python -B scripts/test_verify.py
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ["DB_PATH"] = ":memory:"

from server.assistant.acceptance_verify import (render_results,  # noqa: E402
                                                verify_findings)
from server.contract.csv_rules import RULES as CSV_RULES  # noqa: E402
from server.contract.model import CheckSpec, Criterion  # noqa: E402
from server.verify import check as check_mod  # noqa: E402
from server.verify.check import (SKIPPED_CHECKS, check_csv_assert,  # noqa: E402
                                 check_file_exists_nonempty,
                                 check_syntax_ok)
from server.verify.result import (KIND_DETERMINISTIC, KIND_HUMAN,  # noqa: E402
                                  KIND_SEMANTIC, CriterionResult,
                                  VerificationReport)
from server.verify.verifier import required_of, verify, verify_one  # noqa: E402

PASSED: list[str] = []
FAILED: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    if ok:
        PASSED.append(name)
        print(f"  ok   {name}")
    else:
        FAILED.append(name)
        print(f"  FAIL {name}｜{detail}")


class FakeReader:
    """path -> bytes 的假端口；记录被读过哪些路径，让「没有真 IO」可断言。"""

    def __init__(self, files: dict[str, bytes]) -> None:
        self.files = files
        self.reads: list[str] = []

    def __call__(self, path: str) -> bytes:
        self.reads.append(path)
        if path not in self.files:
            raise FileNotFoundError(2, "No such file or directory", path)
        return self.files[path]


def spec(check_type: str, **fields) -> CheckSpec:
    """执行器（check_*）吃的是 CheckSpec，不是 Criterion——这两个别混。"""
    return CheckSpec(type=check_type, **fields)


def crit(check_type: str, *, cid: str = "c1", kind: str = KIND_DETERMINISTIC,
         **fspec) -> Criterion:
    return Criterion(id=cid, text=f"{check_type} {cid}", kind=kind,
                     check=spec(check_type, **fspec))


# ------------------------------------------------------ 1. file_exists_nonempty

def test_file_exists() -> None:
    print("[file_exists_nonempty]")
    r = FakeReader({"a.md": b"hello", "empty.md": b"", "small.md": b"ab"})
    res = check_file_exists_nonempty("c1", spec("file_exists_nonempty", path="a.md"), r)
    check("存在且非空 -> passed=True", res.passed is True, res.evidence)
    check("evidence 带字节数可审计", "5 字节" in res.evidence, res.evidence)

    res = check_file_exists_nonempty("c2", spec("file_exists_nonempty", path="nope.md"), r)
    check("文件缺失 -> passed=False（这正是 verify 最有价值的场景）",
          res.passed is False and "FileNotFoundError" in res.evidence, res.evidence)

    res = check_file_exists_nonempty("c3", spec("file_exists_nonempty", path="empty.md"), r)
    check("空文件 -> False，evidence 说明 0 字节",
          res.passed is False and "0 字节" in res.evidence, res.evidence)

    res = check_file_exists_nonempty(
        "c4", spec("file_exists_nonempty", path="small.md", min_bytes=10), r)
    check("低于 min_bytes -> False", res.passed is False and "min_bytes" in res.evidence,
          res.evidence)
    res = check_file_exists_nonempty(
        "c5", spec("file_exists_nonempty", path="a.md", min_bytes=5), r)
    check("恰好等于 min_bytes -> True（边界是 >=）", res.passed is True, res.evidence)
    # bool 也是 int 的实例：比较那行显式排除 bool（size < min_bytes 不生效），
    # 但 evidence 的 suffix 行没有排除——min_bytes=True 时仍会被写进话术。
    # 行为面（不误伤判定）钉住即可，措辞瑕疵不锁死。
    res = check_file_exists_nonempty(
        "c6", spec("file_exists_nonempty", path="a.md", min_bytes=True), r)
    check("min_bytes=True 不参与比较（判定不因此误伤）", res.passed is True, res.evidence)

    r2 = FakeReader({})
    res = check_file_exists_nonempty("c7", spec("file_exists_nonempty", path="  "), r2)
    check("空 path -> False 且不触 reader", res.passed is False and r2.reads == [],
          f"{res.evidence} reads={r2.reads}")


# ------------------------------------------------------ 2. syntax_ok

def test_syntax_ok() -> None:
    print("[syntax_ok]")
    py_ok = "import json\n\ndef f():\n    return json.dumps({})\n"
    py_bad = "def f(:\n"
    json_ok = '{"a": 1}'
    json_bad = '{"a": '
    r = FakeReader({"good.py": py_ok.encode(), "bad.py": py_bad.encode(),
                    "good.json": json_ok.encode(), "bad.json": json_bad.encode(),
                    "utf16.json": '{"a": 1}'.encode("utf-16"),
                    "bad_utf8.py": b"def f(): \xff\xfe\x00 return\n"})

    res = check_syntax_ok("s1", spec("syntax_ok", path="good.py", lang="python"), r)
    check("python 语法通过 -> True", res.passed is True, res.evidence)
    res = check_syntax_ok("s2", spec("syntax_ok", path="bad.py", lang="python"), r)
    check("python 语法错误 -> False，evidence 带行号",
          res.passed is False and "第" in res.evidence, res.evidence)
    res = check_syntax_ok("s3", spec("syntax_ok", path="good.json", lang="json"), r)
    check("json 通过 -> True", res.passed is True, res.evidence)
    res = check_syntax_ok("s4", spec("syntax_ok", path="bad.json", lang="json"), r)
    check("json 截断 -> False", res.passed is False, res.evidence)
    res = check_syntax_ok("s5", spec("syntax_ok", path="utf16.json", lang="json"), r)
    check("非 UTF-8 -> False（不是 None：这是缺陷，不是缺口）",
          res.passed is False and "UTF-8" in res.evidence, res.evidence)
    res = check_syntax_ok("s6", spec("syntax_ok", path="bad_utf8.py", lang="python"), r)
    check("坏字节 -> False", res.passed is False, res.evidence)

    n_before = len(r.reads)
    res = check_syntax_ok("s7", spec("syntax_ok", path="good.py", lang="ts"), r)
    check("ts -> passed=None（显式缺口，不猜）",
          res.passed is None and res.note, f"{res.passed} note={res.note}")
    check("ts 缺口不触 reader", len(r.reads) == n_before, str(r.reads[n_before:]))

    res = check_syntax_ok("s8", spec("syntax_ok", path="good.py", lang=""), r)
    check("lang 为空 -> False", res.passed is False and "不是 python/ts/json/yaml" in res.evidence,
          res.evidence)

    r2 = FakeReader({})
    res = check_syntax_ok("s9", spec("syntax_ok", path="gone.py", lang="python"), r2)
    check("syntax_ok 读不到文件 -> False", res.passed is False and "gone.py" in res.evidence,
          res.evidence)


# ------------------------------------------------------ 3. csv_assert

def _rule(name: str, **args) -> object:
    class R:
        pass
    r = R()
    r.name = name
    r.args = args
    return r


def test_csv_assert() -> None:
    print("[csv_assert]")
    good = b"key,state\n1,open\n2,done\n"
    # 单个字段超过 csv.field_size_limit（默认 128KiB）是跨版本稳定的 csv.Error
    # 触发器；未闭合引号/ NUL 的行为随 Python 版本变，拿来钉异常路径会种下 flake。
    r = FakeReader({"ok.csv": good,
                    "empty_cell.csv": b"key,state\n1,\n",
                    "badstate.csv": b"key,state\n1,wat\n",
                    "huge.csv": b"key\n" + b"x" * 200000 + b"\n",
                    "dup.csv": b"key,state\n1,open\n1,done\n"})
    res = check_csv_assert("v1", spec("csv_assert", path="ok.csv",
                                      rules=(_rule("no_empty_cells"),
                                             _rule("unique_key", columns=["key"]))), r)
    check("两条规则全过 -> True", res.passed is True, res.evidence)
    res = check_csv_assert("v2", spec("csv_assert", path="empty_cell.csv",
                                      rules=(_rule("no_empty_cells"),)), r)
    check("空格子 -> False", res.passed is False, res.evidence)
    res = check_csv_assert("v3", spec("csv_assert", path="badstate.csv",
                                      rules=(_rule("column_values_in_set",
                                                   column="state",
                                                   values=["open", "done"]),)), r)
    check("列值越界 -> False", res.passed is False, res.evidence)
    # 超长字段让 csv 模块抛 csv.Error（跨版本稳定），check_csv_assert 把它收进
    # evidence，不是让异常穿透给调用方。
    res = check_csv_assert("v4", spec("csv_assert", path="huge.csv",
                                      rules=(_rule("no_empty_cells"),)), r)
    check("坏 CSV -> False（csv.Error 被收进 evidence）",
          res.passed is False and "不是合法 CSV" in res.evidence, res.evidence)
    res = check_csv_assert("v5", spec("csv_assert", path="dup.csv",
                                      rules=(_rule("unique_key", columns=["key"]),)), r)
    check("唯一键冲突 -> False", res.passed is False, res.evidence)
    res = check_csv_assert("v6", spec("csv_assert", path="ok.csv",
                                      rules=(_rule("nonexistent_rule"),)), r)
    check("未知规则名 -> False，evidence 列出可用规则（不静默跳过）",
          res.passed is False and "未知规则" in res.evidence, res.evidence)

    res = check_csv_assert("v7", spec("csv_assert", path="ok.csv",
                                      rules=(_rule("header_equals_source"),)), r)
    check("对比类规则缺原表 -> passed=None + note（不猜、不假装通过）",
          res.passed is None and res.note and "header_equals_source" in res.evidence,
          f"{res.passed} {res.evidence}")
    res = check_csv_assert("v8", spec("csv_assert", path="ok.csv",
                                      rules=(_rule("header_equals_source"),
                                             _rule("no_empty_cells"))), r)
    check("可判的过了、缺原表的让整条降为 None 而不是 True",
          res.passed is None and "通过 1 条" in res.evidence, res.evidence)
    res = check_csv_assert("v9", spec("csv_assert", path="ok.csv",
                                      rules=(_rule("header_equals_source"),)), r,
                           source_rows=[["key", "state"]])
    check("注入 source_rows 后可判 -> True", res.passed is True, res.evidence)

    res = check_csv_assert("v10", spec("csv_assert", path="ok.csv", rules=()), r)
    check("没有声明规则 -> False", res.passed is False and "没有声明任何规则" in res.evidence,
          res.evidence)


# ------------------------------------------------------ 4. verify() 分派与三态

def test_verify_dispatch() -> None:
    print("[verify 分派 / 三态 / kind]")
    r = FakeReader({"a.md": b"x"})
    report = verify([crit("file_exists_nonempty", path="a.md", cid="p"),
                    crit("file_exists_nonempty", path="gone.md", cid="f"),
                    crit("rubric", cid="s", kind=KIND_SEMANTIC, rubric=("像样",)),
                    crit("ask_user", cid="h", kind=KIND_HUMAN, question="可以吗？")],
                   reader=r)
    by = {x.criterion_id: x for x in report.results}
    check("四条都产出结果", len(report.results) == 4, str(len(report.results)))
    check("真过的 True", by["p"].passed is True, by["p"].evidence)
    check("真不过的 False", by["f"].passed is False, by["f"].evidence)
    check("SKIPPED_CHECKS 的四种 -> passed=None 且带原因",
          by["s"].passed is None and by["h"].passed is None
          and "未实现" in by["s"].evidence, f"{by['s'].evidence}|{by['h'].evidence}")
    check("semantic/human 保留原 kind",
          by["s"].kind == KIND_SEMANTIC and by["h"].kind == KIND_HUMAN,
          f"{by['s'].kind}/{by['h'].kind}")
    check("needs_judge/needs_human 分开", len(report.needs_judge) == 1
          and len(report.needs_human) == 1, f"{report.needs_judge}|{report.needs_human}")
    check("有未判定项时 all_passed=False", report.all_passed is False)
    check("to_bool_tuple 把 None 投影成 False（未判定≠通过）",
          report.to_bool_tuple() == (True, False, False, False), str(report.to_bool_tuple()))

    no_check = Criterion(id="n", text="t", kind=KIND_DETERMINISTIC, check=None)
    res = verify_one(no_check, reader=r)
    check("check=None -> False 且有指向 lint L1 的证据",
          res.passed is False and "lint" in res.evidence, res.evidence)
    res = verify_one(crit("totally_unknown_type", cid="u"), reader=r)
    check("未知 check.type -> False（不静默）", res.passed is False and "未知" in res.evidence,
          res.evidence)
    res = verify_one(Criterion(id="", text="t", kind=KIND_DETERMINISTIC,
                               check=CheckSpec(type="file_exists_nonempty", path="a.md")),
                     reader=r)
    check("缺 id 的 criterion 按序号标注，不静默跳过",
          res.criterion_id.startswith("criterion["), res.criterion_id)
    check("required_of 默认算 required、只排显式 False",
          len(required_of([Criterion(id="x", text="t", kind=KIND_DETERMINISTIC,
                                     check=None, required=False),
                           Criterion(id="y", text="t", kind=KIND_DETERMINISTIC,
                                     check=None)])) == 1)
    check("空 criteria -> 空报告不是 None",
          verify((), reader=r).results == () and verify(None, reader=r).results == ())
    check("to_mapping 形状含 id/kind/passed/evidence",
          set(report.results[0].to_mapping()) >= {"id", "kind", "passed", "evidence"})
    check("已判定但无 evidence -> 构造期即报错（可审计是硬约束）",
          _raises(ValueError, lambda: CriterionResult(
              criterion_id="z", kind=KIND_DETERMINISTIC, passed=True, evidence="  ")))
    check("未判定可以没有 evidence",
          _raises_ok(lambda: CriterionResult(
              criterion_id="z", kind=KIND_DETERMINISTIC, passed=None)))


def _raises(exc_type: type, fn) -> bool:
    try:
        fn()
    except exc_type:
        return True
    except Exception:
        return False
    return False


def _raises_ok(fn) -> bool:
    try:
        fn()
        return True
    except Exception:
        return False


# ------------------------------------------------------ 5. IO 禁令机械检查

# check.py / verifier.py 的 docstring 承诺「禁止开 socket、连库、subprocess」。
# 承诺要有门卫：这里对 server/verify/**.py 做静态扫描。口径与
# AGENTS.md 的死链教训一致——**一个有洞的校验器报「全 OK」比没扫更危险**，
# 所以每条禁令都用违规样例自证能命中（test_io_scanner_self_proof）。
IO_FORBIDDEN_CALLS = {
    "socket": ("socket.socket", "socket.create_connection", "socket.bind"),
    "subprocess": ("subprocess.run", "subprocess.Popen", "subprocess.call",
                   "os.system", "os.popen", "os.execv"),
    "sqlite": ("sqlite3.connect", "sqlite.connect"),
    "http": ("httpx.Client", "httpx.AsyncClient", "requests.get", "requests.post",
             "urllib.request.urlopen"),
}
IMPORT_FORBIDDEN = ("server.assistant", "server.dialogue", "server.memory",
                    "server.interaction", "server.broadcast", "httpx", "requests",
                    "sqlite3", "socket", "subprocess")
# server.verify 的既有例外（方向与理由）：
# - server.task：check.py 头部写明「方向是 verify -> task，纯函数依赖」；
# - server.contract：verifier 的 Criterion/CheckSpec/csv_rules 来自契约层，
#   test_task_fsm 禁的是反方向（task 不许 import verify）。
ALLOWED_SERVER_IMPORTS = ("server.task", "server.contract", "server.verify")


def _verify_sources() -> list[Path]:
    return sorted((ROOT / "server" / "verify").glob("*.py"))


def _imported_roots(tree: ast.AST) -> list[str]:
    """模块里出现过的 import 根名（含函数体内的懒 import——那正是绕过手法）。

    相对导入按被扫文件的位置（server/verify/ 下）解析：level=1 是包内，
    level=2 出到 server.<x>。**不能把一切相对导入都记成 server.verify**——
    那样 `from ..assistant import evaluator` 会伪装成包内依赖绕过禁令，
    一个有洞的校验器报「零违规」比不报更危险。
    """
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                if node.module:
                    found.append(node.module)
            elif node.level == 1:
                found.append("server.verify." + (node.module or ""))
            elif node.level == 2:
                found.append("server." + (node.module or ""))
            else:
                # level>=3 出到 server 之上（仓库根或第三方），原样记模块名。
                found.append(node.module or "<above-root>")
    return found


def _called_dotted(tree: ast.AST) -> list[str]:
    """收集形如 a.b(...) 的调用点全名（两/三段），用于禁令匹配。"""
    names: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        parts: list[str] = []
        cur = node.func
        while isinstance(cur, ast.Attribute):
            parts.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name):
            parts.append(cur.id)
            names.append(".".join(reversed(parts)))
    return names


def test_io_ban() -> None:
    print("[IO 禁令静态扫描]")
    sources = _verify_sources()
    check("扫到了 server/verify 的源文件（扫描器不空转）", len(sources) >= 3,
          str([p.name for p in sources]))
    violations: list[str] = []
    for path in sources:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in _called_dotted(tree):
            for kind, prefixes in IO_FORBIDDEN_CALLS.items():
                if call.startswith(tuple(prefixes)):
                    violations.append(f"{path.name}: 调用 {call}（{kind}）")
        for mod in _imported_roots(tree):
            top = ".".join(mod.split(".")[:2])
            if any(mod.startswith(f) for f in ALLOWED_SERVER_IMPORTS):
                continue
            if any(mod == f or mod.startswith(f + ".") for f in IMPORT_FORBIDDEN):
                violations.append(f"{path.name}: import {mod}")
    check("三个模块零 IO / 零越界 import", not violations, "；".join(violations))

    # 默认 reader 的形态钉住：它是本包唯一的真文件系统出口，
    # 必须是 Path(...).read_bytes —— 换成 open(...,'rb') 之外的一切（例如
    # 顺手加个 socket 探活）都应被上面的扫描抓住。
    src = (ROOT / "server" / "verify" / "check.py").read_text(encoding="utf-8")
    check("default_reader 仍是 read_bytes（唯一真 IO 出口未漂移）",
          "Path(path).read_bytes()" in src)


def test_io_scanner_self_proof() -> None:
    """扫描器的正例自证：违规样例必须被抓住，否则「零违规」是假的。"""
    print("[扫描器自证]")
    bad_src = (
        "import socket\n"
        "import subprocess\n"
        "import sqlite3\n"
        "import httpx\n"
        "from server.assistant import evaluator\n"
        "def f():\n"
        "    socket.create_connection(('x', 1))\n"
        "    subprocess.run(['ls'])\n"
        "    sqlite3.connect('data/memory.db')\n"
        "    httpx.Client()\n"
    )
    tree = ast.parse(bad_src, filename="<bad>")
    calls = _called_dotted(tree)
    hits = [c for c in calls
            if any(c.startswith(p) for ps in IO_FORBIDDEN_CALLS.values() for p in ps)]
    check("四条禁令的调用样例全部命中",
          {"socket.create_connection", "subprocess.run", "sqlite3.connect",
           "httpx.Client"} <= set(hits), str(hits))
    mods = _imported_roots(tree)
    bad_imports = [m for m in mods
                   if not any(m.startswith(a) for a in ALLOWED_SERVER_IMPORTS)
                   and any(m == f or m.startswith(f + ".") for f in IMPORT_FORBIDDEN)]
    check("越界 import 样例全部命中（含函数体外的直接 import）",
          {"socket", "subprocess", "sqlite3", "httpx",
           "server.assistant"} <= set(bad_imports), str(bad_imports))
    ok_src = "from pathlib import Path\n\ndef f(p):\n    return Path(p).read_bytes()\n"
    ok_tree = ast.parse(ok_src, filename="<ok>")
    check("合法样例不误报（读文件是本包被允许的）",
          not [c for c in _called_dotted(ok_tree)
               if any(c.startswith(p) for ps in IO_FORBIDDEN_CALLS.values() for p in ps)]
          and not [m for m in _imported_roots(ok_tree)
                   if any(m == f or m.startswith(f + ".") for f in IMPORT_FORBIDDEN)])
    lazy = "def f():\n    import socket\n    socket.socket()\n"
    lazy_tree = ast.parse(lazy, filename="<lazy>")
    check("函数体内的懒 import 同样被抓（绕过路径已封）",
          "socket" in _imported_roots(lazy_tree)
          and "socket.socket" in _called_dotted(lazy_tree))


# ------------------------------------------------------ 6. evaluator 桥接

def _artifact(items: list[dict]) -> dict:
    return {"summary": "s", "artifacts": items}


def test_bridge() -> None:
    print("[acceptance_verify 桥接]")
    tmp = Path(tempfile.mkdtemp(prefix="verify-bridge-"))
    (tmp / "good.json").write_text('{"a": 1}', encoding="utf-8")
    (tmp / "out.md").write_text("内容", encoding="utf-8")
    ref = lambda p: str(tmp / p)  # noqa: E731 - 测试内联便利

    card = _artifact([{"kind": "file", "ref": ref("out.md")},
                      {"kind": "file", "ref": ref("good.json")}])
    f = verify_findings([{"kind": "artifact_exists", "name": "out.md"}], card)
    check("可达文件 + artifact_exists -> 零 finding", f == [], str(f))
    f = verify_findings([{"kind": "artifact_json", "name": "good.json"}], card)
    check("合法 JSON + artifact_json -> 零 finding（syntax_ok json）", f == [], str(f))

    missing = _artifact([{"kind": "file", "ref": ref("gone.md")}])
    f = verify_findings([{"kind": "artifact_exists", "name": "gone.md"}], missing)
    check("声明了但文件缺失 -> blocker finding，rule_id 带序号",
          len(f) == 1 and f[0]["severity"] == "blocker"
          and f[0]["rule_id"] == "verify:file_exists_nonempty[0]"
          and "gone.md" in f[0]["detail"], str(f))

    f = verify_findings([{"kind": "artifact_exists", "name": "a/../evil.md"}], card)
    check("name 带路径 -> blocker（复用 evaluator 的判定，不抄第三份规则）",
          len(f) == 1 and "路径" in f[0]["detail"], str(f))

    note_only = _artifact([{"kind": "note", "ref": "note:x@1"}])
    f = verify_findings([{"kind": "artifact_exists", "name": "x@1"}], note_only)
    check("note 类 ref 不当文件读 -> 判「没有声明」而非误打回（MAJOR-1）",
          len(f) == 1 and "没有名为" in f[0]["detail"], str(f))

    f = verify_findings([{"kind": "artifact_json"}], card)
    check("无 name 的 artifact_json -> 跳过（至少一个的语义留给 evaluator）",
          f == [], str(f))
    f = verify_findings([{"kind": "artifact_contains", "needle": "内容"}], card)
    check("未映射的 kind -> 桥接零追加（不造第二份真相源）", f == [], str(f))
    f = verify_findings([{"kind": "artifact_exists", "name": "out.md"}], None)
    check("artifact=None -> 不炸，判「没有名为 out.md 的成果声明」",
          len(f) == 1 and f[0]["severity"] == "blocker" and "没有名为" in f[0]["detail"],
          str(f))
    f = verify_findings("not-a-list", card)
    check("rules 非列表 -> 返回 [] 不抛", f == [], str(f))

    # 渲染契约：三态各自去向，None 绝不进 findings。
    res = [CriterionResult("a", KIND_DETERMINISTIC, True, "过了"),
           CriterionResult("b", KIND_DETERMINISTIC, False, "没过"),
           CriterionResult("c", KIND_DETERMINISTIC, None, "", note="不归我判")]
    findings, notes = render_results(res)
    check("True -> 无声", all(x["rule_id"] != "a" for x in findings), str(findings))
    check("False -> blocker finding，detail 取 evidence",
          len(findings) == 1 and findings[0]["rule_id"] == "b"
          and findings[0]["detail"] == "没过", str(findings))
    check("None -> 只进 notes，绝不进 findings（防「未判定」被谎报成不合格）",
          len(notes) == 1 and notes[0].startswith("c 未判定")
          and all(x["rule_id"] != "c" for x in findings), f"{findings}|{notes}")

    empty = _artifact([])
    f = verify_findings([{"kind": "artifact_exists"}], empty)
    check("交付卡没有任何声明 -> blocker 说明没有可达 ref",
          len(f) == 1 and "没有声明任何可达 ref" in f[0]["detail"], str(f))


def test_skipped_checks_are_explicit() -> None:
    print("[SKIPPED_CHECKS 显式缺口]")
    check("四种明确不做，各带一句原因",
          set(SKIPPED_CHECKS) == {"schema_valid", "exit_code_zero", "rubric", "ask_user"}
          and all(v.strip() for v in SKIPPED_CHECKS.values()), str(SKIPPED_CHECKS))
    check("做与不做合起来覆盖 ALL_CHECK_TYPES",
          set(check_mod.DETERMINISTIC_EXECUTORS) | set(SKIPPED_CHECKS)
          == set(check_mod.ALL_CHECK_TYPES),
          str(check_mod.ALL_CHECK_TYPES))
    check("csv_assert 的规则面确实接的是 contract 六条",
          set(CSV_RULES) == {"header_equals_source", "row_count_gte_source",
                             "no_empty_cells", "column_values_in_set",
                             "changed_rows_column_is_today", "unique_key"},
          str(sorted(CSV_RULES)))


def main() -> int:
    test_file_exists()
    test_syntax_ok()
    test_csv_assert()
    test_verify_dispatch()
    test_io_ban()
    test_io_scanner_self_proof()
    test_bridge()
    test_skipped_checks_are_explicit()
    print(f"\n=== {len(PASSED)} passed, {len(FAILED)} failed ===")
    return 1 if FAILED else 0


if __name__ == "__main__":
    raise SystemExit(main())
