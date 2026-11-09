"""工作区根解析契约：相对值绝对化到**固定基准**，绝不按进程 cwd 解析。

**为什么需要它**：2026-11-09 的全量审计（docs/evidence/2026-11-09-db-sentinel） 发现
`gate_tmp_leak` / `step8_e2e_browser` / `drill_ws_reconnect` 三条门禁都在往真实
`data/commissions/<id>/out/verify_report.json` 写盘。根因不是门禁没做隔离，而是
花名册里的 `AQI_WORKSPACE_ROOT: "data/commissions"` 是相对值：

- `cli_subproc.prepare_context` 把花名册 env 原样塞进子进程 env；
- shim 用 `Path(root).resolve()` 按**进程 cwd** 解析；
- `durable._workspace()` 在服务进程里同样按 cwd 解析。

门禁进程的 cwd 恰是仓库根，于是「临时根隔离」在台账层成立、在产物层失效——文件
照样落进真实 `data/commissions/`。更隐蔽的后果：一旦两边 cwd 不同，产物写在一处、
验收按另一处回读（`artifacts_exist` 打在 file 上会判不存在），看起来像"执行体没产出"。

**本契约钉四件事**：

1. 相对值基准固定为 `config.service_data_dir()`（生产 `<repo>/data`；哨兵进程
   `TEST_SENTINEL=1` 时跟随 `DB_PATH` / `APP_DATA_DIR` / 进程临时目录），与 cwd 无关；
2. 三层都要覆到：默认配置值、显式覆盖（适配器配置 / 进程 env `AQI_WORKSPACE_ROOT`）、
   测试注入临时根；
3. `cli_subproc` 下发给子进程的必须是**绝对路径**，`durable._workspace()` 用同一个
   解析器算——这是"产物回读仍找得到"的前提；
4. 没配工作区根时 durable 仍返回空串（sidecar 那时不允许写文件，不替人猜目录）。

Run: .venv/Scripts/python.exe -B scripts/test_workspace_root.py
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 本进程先把自己钉进隔离根：留一份「测试注入临时根」的证据，也让任何意外落到
# 真实库的路径当场被 guard_sqlite_path 拦下，而不是事后靠快照才发现。
ISOLATED = Path(tempfile.mkdtemp(prefix="workspace-root-"))
os.environ["TEST_SENTINEL"] = "1"
os.environ["DB_PATH"] = str(ISOLATED / "memory.db")
sys.path.insert(0, str(ROOT))

from server import config  # noqa: E402

passed, failed = 0, []
try:
    from server.assistant.adapters.workspace import resolve_workspace_root  # noqa: E402
    IMPORT_ERROR = ""
except Exception as _exc:  # 红阶段：模块还不存在，别用 ImportError 掩盖真实断言
    resolve_workspace_root = None
    IMPORT_ERROR = f"{type(_exc).__name__}: {_exc}"

from server.assistant.adapters.cli_subproc import CliSubprocessAdapter  # noqa: E402
from server.assistant.adapters.durable import DurableAdapter  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

SHIM = ROOT / "scripts" / "worker_shims" / "sandbox_worker.py"


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f"  ｜{detail}" if detail else ""))


def resolve(*args, **kwargs):
    """解析器的薄包装：模块缺失时返回 None，让断言留在语义层而不是 import 层。"""
    if resolve_workspace_root is None:
        return None
    return resolve_workspace_root(*args, **kwargs)


def service_base() -> Path:
    """`config.service_data_dir()` 的薄包装：红阶段还没这个函数，用隔离根当期望值。

    这不会把红洗成绿——「函数必须存在」由 test_resolver_layers 里单独一条断言钉住；
    其余断言拿隔离根当基准，验的是「哨兵下相对值必须落到临时根」这件语义。
    """
    fn = getattr(config, "service_data_dir", None)
    return Path(fn()) if callable(fn) else ISOLATED


@contextmanager
def in_cwd(target: Path):
    old = os.getcwd()
    os.chdir(target)
    try:
        yield
    finally:
        os.chdir(old)


def sandbox_entry() -> dict:
    roster = load_roster()
    for item in roster.get("agents") or []:
        if isinstance(item, dict) and item.get("id") == "sandbox":
            return dict(item)
    raise AssertionError("花名册里没有 sandbox 条目")


def snapshot(path: Path) -> tuple:
    """只记顶层条目名：这些门禁漏的正是「新建一单目录」。"""
    if not path.exists():
        return ()
    return tuple(sorted(item.name for item in path.iterdir()))


def test_resolver_layers() -> None:
    print("[解析器：三层基准 + cwd 无关]")
    check("config.service_data_dir 存在（基准的唯一出处）",
          callable(getattr(config, "service_data_dir", None)))
    svc = service_base()
    check("service_data_dir 是绝对路径", svc.is_absolute(), str(svc))
    check("哨兵进程跟随 DB_PATH 的临时根", svc.resolve() == ISOLATED.resolve(),
          f"svc={svc} 期望={ISOLATED}")
    check("service_data_dir 不落在仓库 data/",
          (ROOT / "data").resolve() not in svc.resolve().parents, str(svc))
    if resolve_workspace_root is None:
        check("解析器存在（server/assistant/adapters/workspace.py）", False, IMPORT_ERROR)
        return

    default = resolve(None)
    check("默认值解析成绝对路径", isinstance(default, str) and Path(default).is_absolute(),
          str(default))
    check("默认值 = <服务数据目录>/commissions",
          default is not None and Path(default) == (svc / "commissions").resolve(), str(default))
    legacy = resolve("data/commissions")
    check("相对值走同一基准（老写法不再指仓库 data/）",
          legacy == str((svc / "data" / "commissions").resolve()), str(legacy))

    explicit = ISOLATED / "explicit-ws"
    check("显式绝对路径原样采纳", resolve(str(explicit)) == str(explicit.resolve()),
          str(resolve(str(explicit))))

    env_root = ISOLATED / "env-ws"
    saved = os.environ.get("AQI_WORKSPACE_ROOT")
    os.environ["AQI_WORKSPACE_ROOT"] = str(env_root)
    try:
        check("进程 env 覆盖在无配置时生效", resolve(None) == str(env_root.resolve()),
              str(resolve(None)))
    finally:
        if saved is None:
            os.environ.pop("AQI_WORKSPACE_ROOT", None)
        else:
            os.environ["AQI_WORKSPACE_ROOT"] = saved

    other = ISOLATED / "cwd-probe"
    other.mkdir(parents=True, exist_ok=True)
    with in_cwd(other):
        here = resolve("commissions")
    with in_cwd(ROOT):
        there = resolve("commissions")
    check("同一相对值在两个 cwd 下结果一致", here == there, f"{here} / {there}")
    check("结果与 cwd 无关（== 基准/commissions）",
          here == str((svc / "commissions").resolve()), str(here))


def test_cli_subproc_delivery() -> None:
    print("[cli_subproc：下发给子进程的值]")
    entry = sandbox_entry()
    adapter = CliSubprocessAdapter(entry)
    ctx = asyncio.run(adapter.prepare_context("c_ws_1", {"objective": "x"}))
    value = ctx["env"].get("AQI_WORKSPACE_ROOT")
    check("下发的根是绝对路径",
          isinstance(value, str) and Path(value).is_absolute(), str(value))
    expected = resolve((entry.get("env") or {}).get("AQI_WORKSPACE_ROOT"))
    check("下发的根 = 解析器结果", value == expected, f"{value} 期望={expected}")

    other = ISOLATED / "cwd-probe-2"
    other.mkdir(parents=True, exist_ok=True)
    with in_cwd(other):
        moved = asyncio.run(adapter.prepare_context("c_ws_1", {"objective": "x"}))
    check("换 cwd 下发的根不变", moved["env"].get("AQI_WORKSPACE_ROOT") == value,
          f"{moved['env'].get('AQI_WORKSPACE_ROOT')} / {value}")


def test_durable_workspace() -> None:
    print("[durable：回读侧同源]")
    adapter = DurableAdapter({"workspace_root": "commissions"})
    workspace = Path(adapter._workspace("c_ws_2"))
    check("每单目录是绝对路径", workspace.is_absolute(), str(workspace))
    expected = resolve("commissions")
    check("每单目录挂在解析后的根下",
          expected is not None and workspace.parent == Path(expected),
          f"{workspace.parent} 期望={expected}")
    other = ISOLATED / "cwd-probe-3"
    other.mkdir(parents=True, exist_ok=True)
    with in_cwd(other):
        again = Path(adapter._workspace("c_ws_2"))
    check("换 cwd 每单目录的根不变", again.parent == workspace.parent,
          f"{again.parent} / {workspace.parent}")
    check("未配置时返回空（sidecar 不允许写文件）",
          DurableAdapter({})._workspace("c_ws_3") == "")
    saved = os.environ.get("AQI_WORKSPACE_ROOT")
    os.environ["AQI_WORKSPACE_ROOT"] = str(ISOLATED / "ambient-ws")
    try:
        check("未配置时不被宿主 shell 的 env 顶开（留空=明确关闭）",
              DurableAdapter({})._workspace("c_ws_4") == "")
    finally:
        if saved is None:
            os.environ.pop("AQI_WORKSPACE_ROOT", None)
        else:
            os.environ["AQI_WORKSPACE_ROOT"] = saved


def test_shim_receives_absolute_root() -> None:
    print("[shim：拿到绝对根就落在绝对根]")
    root = ISOLATED / "shim-ws"
    env = {**os.environ, "AQI_WORKSPACE_ROOT": str(root),
           "AQI_COMMISSION_ID": "c_shim",
           "AQI_BRIEFING_PACKET": json.dumps({"objective": "shim 绝对根探针"})}
    env.pop("AQI_OUTDIR", None)
    proc = subprocess.run([sys.executable, "-B", str(SHIM)], cwd=str(ISOLATED),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=90)
    files = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("type") == "artifact":
            files = [item.get("ref") for item in (event.get("artifact") or {}).get("artifacts") or []
                     if item.get("kind") == "file"]
    check("shim 交了文件成果", bool(files), (proc.stdout or "")[-300:])
    if files:
        target = Path(files[0])
        check("产物落在传入的绝对根下", root.resolve() in target.resolve().parents,
              str(target))
        check("产物存在且可回读", target.is_file(), str(target))
        check("产物不在仓库 data/ 下",
              (ROOT / "data").resolve() not in target.resolve().parents, str(target))


def test_real_chain_readback() -> None:
    """默认配置这一层走真实链：产物落临时根、验收仍回读得到、真实 data/ 不动。"""
    print("[真实链路：默认配置 + 验收回读]")
    roster = load_roster()
    entry = sandbox_entry()
    entry["executable"] = sys.executable
    entry["args"] = [str(SHIM)]
    # 故意**不动** entry["env"]：验的就是「花名册默认值」这一层。
    roster["agents"] = [entry]
    real_commissions = ROOT / "data" / "commissions"
    before = snapshot(real_commissions)
    service = ExecutiveService(ISOLATED / "exec-chain", roster=roster)
    try:
        result = asyncio.run(service.delegate(
            "沙盒打样：受控生成并校验一个小文件",
            acceptance=[{"kind": "artifact_json", "name": "verify_report.json",
                         "expect": {"status": "verified"}}]))
        commission = service.commission(result["commission_id"])
        delivery = commission.get("delivery") or {}
        files = [item for item in delivery.get("artifacts") or []
                 if item.get("kind") == "file"]
        ref = Path(files[0]["ref"]) if files else None
        base = service_base() / "commissions"
        check("验收通过（初审回查到了真文件）", result["status"] == "ready_to_report",
              f"{result['status']}｜{commission.get('blocked_question', '')}")
        check("产物落在 <服务数据目录>/commissions 下",
              ref is not None and base.resolve() in ref.resolve().parents,
              f"{ref} 期望根={base}")
        check("产物存在且含本单号",
              ref is not None and ref.is_file()
              and result["commission_id"] in ref.read_text(encoding="utf-8"), str(ref))
    finally:
        service.close()
    after = snapshot(real_commissions)
    check("真实 data/commissions 前后无变化", before == after,
          f"前={len(before)} 后={len(after)} 新增={sorted(set(after) - set(before))}")


def test_production_base() -> None:
    """无哨兵时基准必须是仓库 data/，且与 cwd 无关。"""
    print("[生产基准]")
    env = {k: v for k, v in os.environ.items()
           if k not in ("TEST_SENTINEL", "DB_PATH", "APP_DATA_DIR")}
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from server import config\n"
            "print(config.service_data_dir())" % str(ROOT))
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(ISOLATED),
                          capture_output=True, text=True, encoding="utf-8",
                          errors="replace", env=env, timeout=60)
    lines = [line.strip() for line in (proc.stdout or "").splitlines() if line.strip()]
    got = Path(lines[-1]) if lines else None
    check("无哨兵时基准是仓库 data/（cwd 不是仓库根）",
          got is not None and got.resolve() == (ROOT / "data").resolve(),
          f"{got}｜{(proc.stderr or '')[-300:]}")


def main() -> int:
    for test in (test_resolver_layers, test_cli_subproc_delivery, test_durable_workspace,
                 test_shim_receives_absolute_root, test_real_chain_readback,
                 test_production_base):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("  -", name)
    shutil.rmtree(ISOLATED, ignore_errors=True)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
