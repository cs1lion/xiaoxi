"""Real worker suite: run the same task families through every live agent.

Manual, costs tokens, needs the local gateways:

    .\\.venv\\Scripts\\python.exe scripts\\probe_live_workers.py --workers claude,codex,workbuddy,dsh

The suite is an oracle, not a cheerleader. Each case declares acceptance rules
twice: once in the Briefing Packet (so the commander can check what it asked
for) and once as an external check in this file (so a buggy evaluator cannot
turn a bad artifact into a pass). `--dry-run` prints the plan without calling a
model.

Safety: Codex needs `danger-full-access` on this Windows host because its
`workspace-write` sandbox fails to start (verified 2026-10-17). It is therefore
opt-in here and never added to the production roster by this script.
"""
from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.assistant.executive import ExecutiveService  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402

PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
SHIMS = ROOT / "scripts" / "worker_shims"


@dataclass(frozen=True)
class Case:
    id: str
    family: str
    task: str
    setup: dict[str, str] = field(default_factory=dict)
    acceptance: tuple[dict[str, Any], ...] = ()
    # External oracle: returns a list of human-readable problems.
    oracle: Any = None


def _file(name: str, contains: str | None = None, expect: dict | None = None,
          regex: str | None = None, max_bytes: int | None = None) -> dict[str, Any]:
    rule: dict[str, Any] = {"kind": "artifact_name", "name": name}
    if contains is not None:
        rule = {"kind": "artifact_contains", "name": name, "text": contains}
    elif expect is not None:
        rule = {"kind": "artifact_json", "name": name, "expect": expect}
    elif regex is not None:
        rule = {"kind": "artifact_regex", "name": name, "pattern": regex}
    elif max_bytes is not None:
        rule = {"kind": "artifact_max_bytes", "name": name, "bytes": max_bytes}
    return rule


def _out(workdir: Path, name: str) -> Path:
    return workdir / "out" / name


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _oracle_note(workdir: Path) -> list[str]:
    path = _out(workdir, "note.txt")
    if not path.is_file():
        return ["missing out/note.txt"]
    return [] if "OK" in _read(path) else ["note.txt has no OK"]


def _oracle_json(workdir: Path) -> list[str]:
    path = _out(workdir, "result.json")
    if not path.is_file():
        return ["missing out/result.json"]
    try:
        value = json.loads(_read(path))
    except ValueError:
        return ["result.json is not JSON"]
    return [] if value.get("status") == "ok" and value.get("count") == 3 else [f"wrong value {value!r}"]


def _oracle_transform(workdir: Path) -> list[str]:
    path = _out(workdir, "upper.txt")
    if not path.is_file():
        return ["missing out/upper.txt"]
    return [] if "HELLO WORKER" in _read(path).upper() else ["upper.txt not uppercased"]


def _oracle_chinese(workdir: Path) -> list[str]:
    path = _out(workdir, "chinese.txt")
    if not path.is_file():
        return ["missing out/chinese.txt"]
    return [] if "小溪今天很好" in _read(path) else ["chinese.txt text mismatch"]


def _oracle_missing(workdir: Path) -> list[str]:
    problems = []
    if _out(workdir, "secret_copy.txt").exists():
        problems.append("created a placeholder for a missing input")
    return problems


def _oracle_multi(workdir: Path) -> list[str]:
    problems = []
    for name, want in (("a.txt", "ALPHA"), ("b.txt", "BETA")):
        path = _out(workdir, name)
        if not path.is_file():
            problems.append(f"missing out/{name}")
        elif want not in _read(path):
            problems.append(f"{name} mismatch")
    return problems


def _oracle_fizz(workdir: Path) -> list[str]:
    path = _out(workdir, "fizz.py")
    if not path.is_file():
        return ["missing out/fizz.py"]
    try:
        result = subprocess.run([str(PYTHON), str(path)], capture_output=True, text=True,
                                timeout=20, cwd=str(workdir))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"fizz.py did not run: {type(exc).__name__}"]
    text = (result.stdout or "") + (result.stderr or "")
    problems = []
    if "FizzBuzz" not in text:
        problems.append("output has no FizzBuzz")
    if "14" not in text:
        problems.append("output does not reach 15")
    return problems


def _oracle_summary(workdir: Path) -> list[str]:
    path = _out(workdir, "summary.txt")
    if not path.is_file():
        return ["missing out/summary.txt"]
    text = _read(path).strip()
    if "语音" not in text:
        return ["summary misses the keyword 语音"]
    if len(text) > 120:
        return [f"summary too long ({len(text)} chars)"]
    return []


def _oracle_counts(workdir: Path) -> list[str]:
    path = _out(workdir, "counts.json")
    if not path.is_file():
        return ["missing out/counts.json"]
    try:
        value = json.loads(_read(path))
    except ValueError:
        return ["counts.json is not JSON"]
    want = {"a.py": 2, "b.py": 3, "c.py": 4}
    return [] if value == want else [f"counts mismatch: {value!r}"]


def _oracle_code(workdir: Path) -> list[str]:
    path = _out(workdir, "fixed.py")
    if not path.is_file():
        return ["missing out/fixed.py"]
    try:
        result = subprocess.run([str(PYTHON), str(path)], capture_output=True, text=True,
                                timeout=20, cwd=str(workdir))
    except (OSError, subprocess.TimeoutExpired) as exc:
        return [f"fixed.py did not run: {type(exc).__name__}"]
    return [] if "5" in (result.stdout or "") else ["fixed.py does not print 5"]


CASES: tuple[Case, ...] = (
    Case("note", "create", "在 out 目录创建 note.txt，内容只有一行 OK",
         acceptance=(_file("note.txt", contains="OK"),), oracle=_oracle_note),
    Case("json", "structure", '在 out 目录创建 result.json，内容为 {"status":"ok","count":3}',
         acceptance=(_file("result.json", expect={"status": "ok", "count": 3}),), oracle=_oracle_json),
    Case("transform", "transform", "读取工作目录的 input.txt，把内容转成大写，写入 out/upper.txt",
         setup={"input.txt": "hello worker\n"},
         acceptance=(_file("upper.txt", contains="HELLO WORKER"),), oracle=_oracle_transform),
    Case("chinese", "text", "在 out 目录创建 chinese.txt，写入这句中文：小溪今天很好",
         acceptance=(_file("chinese.txt", contains="小溪今天很好"),), oracle=_oracle_chinese),
    Case("missing", "negative", "读取工作目录里不存在的 secret.txt，把内容原样写入 out/secret_copy.txt。"
         "做不到就明确失败，不要创建占位文件。",
         acceptance=(), oracle=_oracle_missing),
    Case("multi", "multi-file", "在 out 目录创建两个文件：a.txt 内容 ALPHA，b.txt 内容 BETA",
         acceptance=(_file("a.txt", contains="ALPHA"), _file("b.txt", contains="BETA")),
         oracle=_oracle_multi),
    Case("fizz", "code", "在 out 目录写一个可运行的 fizz.py：打印 1 到 15，遇到 3 的倍数打印 Fizz，"
         "5 的倍数打印 Buzz，15 的倍数打印 FizzBuzz。不要只描述，写出文件。",
         acceptance=(_file("fizz.py", regex=r"FizzBuzz"),), oracle=_oracle_fizz),
    Case("summary", "text", "读取工作目录的 article.txt，用一句中文总结它，写入 out/summary.txt。"
         "必须提到“语音”，不超过 120 个字符。",
         setup={"article.txt": "小溪是一个中文语音陪伴角色。她先听见你说话，再用简短的口语回应；"
                               "背后还有一套慢环负责派单、验收和记忆整理。她不追求像仪表盘一样高效，"
                               "而是把陪伴放在第一位。"},
         acceptance=(_file("summary.txt", contains="语音", max_bytes=480),), oracle=_oracle_summary),
    Case("counts", "multi-file", "统计工作目录 src 下 a.py、b.py、c.py 各有多少行，"
         '写成 out/counts.json，形如 {"a.py": 2, "b.py": 3, "c.py": 4}。',
         setup={"src/a.py": "a = 1\nb = 2\n", "src/b.py": "x = 1\ny = 2\nz = 3\n",
                "src/c.py": "1\n2\n3\n4\n"},
         acceptance=(_file("counts.json", expect={"a.py": 2, "b.py": 3, "c.py": 4}),),
         oracle=_oracle_counts),
    Case("code-fix", "code", "工作目录的 bug.py 里 add 函数写错了。修好它，把修正后的完整脚本写到 "
         "out/fixed.py，并让它在运行时打印 add(2, 3) 的结果。",
         setup={"bug.py": "def add(a, b):\n    return a - b\n\nprint(add(2, 3))\n"},
         acceptance=(_file("fixed.py", regex=r"a\s*\+\s*b"),), oracle=_oracle_code),
)


@dataclass(frozen=True)
class Worker:
    id: str
    shim: str
    env: dict[str, str]
    note: str = ""


def worker_profiles() -> dict[str, Worker]:
    claude = Worker(
        "claude", "claude_worker.py",
        {"AQI_GATEWAY_URL": "http://127.0.0.1:15722",
         "AQI_CLAUDE_MODEL": "claude-opus-5-5",
         "AQI_CLAUDE_TIMEOUT": "360",
         "AQI_WORKSPACE_ROOT": "data/commissions"},
        "Anthropic Messages -> 15722")
    codex = Worker(
        "codex", "codex_worker.py",
        {"AQI_GATEWAY_URL": "http://127.0.0.1:15721/v1",
         "AQI_GATEWAY_MODEL": "step-5-preview",
         "AQI_CODEX_EFFORT": "low",
         "AQI_CODEX_SANDBOX": "danger-full-access"},
        "danger-full-access; workspace-write is broken on this host")
    workbuddy = Worker(
        "workbuddy", "workbuddy_worker.py",
        {"CODEBUDDY_BASE_URL": "http://127.0.0.1:15721/v1",
         "CODEBUDDY_API_KEY": "PROXY_MANAGED",
         "AQI_WORKBUDDY_MODEL": "step-5-preview",
         "AQI_WORKBUDDY_TIMEOUT": "300"},
        "CodeBuddy CLI via Electron-as-Node")
    dsh = Worker(
        "dsh", "dsh_worker.py",
        {"DEEPSEEK_BASE_URL": "http://127.0.0.1:15722",
         "DEEPSEEK_API_KEY": "local",
         "DSH_PERMISSION_MODE": "danger-full-access",
         "AQI_DSH_TIMEOUT": "300"},
        "DSH headless via Electron-as-Node")
    return {item.id: item for item in (claude, codex, workbuddy, dsh)}


def build_roster(worker: Worker, workdir: Path) -> dict[str, Any]:
    roster = load_roster()
    roster.setdefault("dispatch", {})["max_prompt_tokens"] = 80000
    roster["agents"] = [{
        "id": f"live_{worker.id}",
        "name": f"真机 {worker.id}",
        "adapter": "cli_subproc",
        "executable": str(PYTHON),
        "args": [str(SHIMS / worker.shim)],
        "cwd": str(workdir),
        "timeout": 420,
        "env": {"AQI_WORKDIR": str(workdir), **worker.env},
        "summary": "在独立工作目录完成一件真实任务并交付文件",
        "strengths": ["创建", "写入", "读取", "总结", "统计", "修复", "运行", "文件", "目录"],
        "weaknesses": [],
        "cost_tier": "medium",
        "max_concurrent": 1,
        "eval_commands": ["required_fields", "artifacts_exist", "token_budget", "acceptance_met"],
    }]
    return roster


def _delegate_accepts() -> bool:
    return "acceptance" in inspect.signature(ExecutiveService.delegate).parameters


async def run_one(worker: Worker, case: Case, base: Path, run_idx: int) -> dict[str, Any]:
    workdir = (base / worker.id / case.id / f"run-{run_idx}").resolve()
    workdir.mkdir(parents=True, exist_ok=True)
    for name, text in case.setup.items():
        target = workdir / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")
    service = ExecutiveService(workdir / "state", roster=build_roster(worker, workdir))
    started = time.time()
    error = ""
    try:
        kwargs = {"acceptance": list(case.acceptance)} if _delegate_accepts() and case.acceptance else {}
        await service.delegate(case.task, **kwargs)
        commission = service.commission(_latest_id(service))
    except Exception as exc:  # the suite records, it does not hide
        error = f"{type(exc).__name__}: {exc}"[:300]
        commission = {}
    finally:
        service.close()
    elapsed_ms = int((time.time() - started) * 1000)
    audit = (commission.get("audit") or {}) if isinstance(commission, dict) else {}
    usage = audit.get("usage") or {}
    external = list(case.oracle(workdir)) if case.oracle else []
    status = str((commission or {}).get("status") or "")
    # Negative cases pass when the worker declines cleanly; positive cases need ready_to_report.
    expected_status = "blocked" if case.family == "negative" else "ready_to_report"
    status_ok = status == expected_status
    problems = list(external)
    if not status_ok:
        problems.append(f"status={status or '?'} want={expected_status}")
    if error:
        problems.append(error)
    findings = [str(item.get("rule_id") or item.get("rule") or "") for item in (audit.get("findings") or [])]
    return {
        "worker": worker.id,
        "case": case.id,
        "family": case.family,
        "run": run_idx,
        "status": status,
        "passed": not problems,
        "problems": problems,
        "findings": findings,
        "retry_count": int((commission or {}).get("retry_count") or 0),
        "elapsed_ms": elapsed_ms,
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "budget_checked": usage.get("budget_checked"),
        "calls": int(usage.get("calls") or 0),
        "blocked_question": str((commission or {}).get("blocked_question") or "")[:200],
        "workdir": str(workdir),
    }


def _latest_id(service: ExecutiveService) -> str:
    rows = service.commissions()
    if not rows:
        raise RuntimeError("no commission was created")
    return str(rows[0]["id"])


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round((pct / 100) * (len(ordered) - 1)))))
    return ordered[index]


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    workers: dict[str, Any] = {}
    for worker in sorted({row["worker"] for row in rows}):
        mine = [row for row in rows if row["worker"] == worker]
        durations = [row["elapsed_ms"] for row in mine]
        workers[worker] = {
            "runs": len(mine),
            "passed": sum(1 for row in mine if row["passed"]),
            "pass_rate": round(sum(1 for row in mine if row["passed"]) / len(mine), 3) if mine else 0,
            "median_ms": int(statistics.median(durations)) if durations else 0,
            "p95_ms": _percentile(durations, 95),
            "prompt_tokens": sum(row["prompt_tokens"] for row in mine),
            "completion_tokens": sum(row["completion_tokens"] for row in mine),
        }
    families: dict[str, Any] = {}
    for family in sorted({row["family"] for row in rows}):
        mine = [row for row in rows if row["family"] == family]
        families[family] = {
            "runs": len(mine),
            "passed": sum(1 for row in mine if row["passed"]),
            "pass_rate": round(sum(1 for row in mine if row["passed"]) / len(mine), 3) if mine else 0,
        }
    return {
        "runs": len(rows),
        "passed": sum(1 for row in rows if row["passed"]),
        "failed": sum(1 for row in rows if not row["passed"]),
        "elapsed_ms": sum(row["elapsed_ms"] for row in rows),
        "prompt_tokens": sum(row["prompt_tokens"] for row in rows),
        "completion_tokens": sum(row["completion_tokens"] for row in rows),
        "workers": workers,
        "families": families,
    }


def print_table(rows: list[dict[str, Any]]) -> None:
    print(f"{'worker':<10} {'case':<10} {'family':<12} {'result':<6} {'ms':>7} {'ptok':>7}  detail")
    for row in rows:
        detail = "; ".join(row["problems"])[:90]
        print(f"{row['worker']:<10} {row['case']:<10} {row['family']:<12} "
              f"{'PASS' if row['passed'] else 'FAIL':<6} {row['elapsed_ms']:>7} "
              f"{row['prompt_tokens']:>7}  {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", default="claude,workbuddy,dsh",
                        help="comma list: claude,codex,workbuddy,dsh,all")
    parser.add_argument("--cases", default="", help="comma list of case ids")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    profiles = worker_profiles()
    wanted = list(profiles) if args.workers == "all" else [w.strip() for w in args.workers.split(",") if w.strip()]
    unknown = [w for w in wanted if w not in profiles]
    if unknown:
        print(f"unknown workers: {unknown}", file=sys.stderr)
        return 2
    cases = [c for c in CASES if not args.cases or c.id in {x.strip() for x in args.cases.split(",")}]
    if args.dry_run:
        print(json.dumps({
            "workers": {w: profiles[w].note for w in wanted},
            "cases": [{"id": c.id, "family": c.family, "acceptance": list(c.acceptance)} for c in cases],
            "repeat": args.repeat,
            "acceptance_supported": _delegate_accepts(),
        }, ensure_ascii=False, indent=2))
        return 0

    stamp = time.strftime("%Y%m%d-%H%M%S")
    base = (ROOT / "tmp" / f"live-workers-{stamp}").resolve()
    base.mkdir(parents=True, exist_ok=True)
    out_dir = (ROOT / "eval" / f"live-workers-{stamp}").resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    for run_idx in range(1, max(1, args.repeat) + 1):
        for worker_id in wanted:
            for case in cases:
                print(f"[{worker_id}] {case.id} run {run_idx} ...", flush=True)
                row = asyncio.run(run_one(profiles[worker_id], case, base, run_idx))
                rows.append(row)
                print(f"  {'PASS' if row['passed'] else 'FAIL'} {row['elapsed_ms']}ms "
                      f"{row['prompt_tokens']}ptok {'; '.join(row['problems'])[:120]}", flush=True)
    summary = summarize(rows)
    payload = {"at": stamp, "base": str(base), "summary": summary, "rows": rows}
    (out_dir / "summary.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [f"# Live worker suite {stamp}", "",
             f"- runs: {summary['runs']}  passed: {summary['passed']}  failed: {summary['failed']}",
             f"- prompt tokens: {summary['prompt_tokens']}  completion tokens: {summary['completion_tokens']}",
             "", "| worker | runs | pass | median ms | p95 ms | prompt tokens |",
             "|---|---:|---:|---:|---:|---:|"]
    for worker, data in summary["workers"].items():
        lines.append(f"| {worker} | {data['runs']} | {data['passed']}/{data['runs']} | "
                     f"{data['median_ms']} | {data['p95_ms']} | {data['prompt_tokens']} |")
    lines += ["", "| family | runs | pass |", "|---|---:|---:|"]
    for family, data in summary["families"].items():
        lines.append(f"| {family} | {data['runs']} | {data['passed']}/{data['runs']} |")
    (out_dir / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print_table(rows)
    print(json.dumps(summary["workers"], ensure_ascii=False, indent=2))
    print(f"wrote {out_dir / 'summary.json'}")
    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
