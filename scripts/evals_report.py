"""评测报告层：读 evals/results/ 的运行快照，出通过率、配对差异与成本。

    .\\.venv\\Scripts\\python.exe scripts\\evals_report.py
    .\\.venv\\Scripts\\python.exe scripts\\evals_report.py --json evals\\results\\report.json

分层语义（见 docs/EVAL_MECHANISM_PLAN.md）：
- 本脚本属于「报告」区：只读快照、只出报告，**不计入任何退出码**，失败也退出 0。
- 「报告跑得完」本身由 run_gates 的 evals_report 门禁断言（可执行性，不是内容）。
- 只读：不写、不删、不轮转 evals/results/。删除本地证据属数据红线。

为什么需要它：run_evals.py 每次运行只落一份带时间戳的快照，全仓没有任何读取方；
本脚本是第一个消费者，提供趋势、配对比较与成本记账三样原本缺失的东西。

通过率的两个数必须分开看：
- 平均通过率 = passed_runs / total_runs，回答「有能力吗」；
- 全过概率   = 平均通过率 ^ total_runs（pass^k 估计），回答「稳定吗」。
混成一个数字时，「有能力但不稳定」与「能力不足」不可区分。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RESULTS_DIR = ROOT / "evals" / "results"

# Windows GBK 控制台曾让同类报告脚本崩过（run_gates 的 evolution_quality_report
# 注释记有此事）；强制 UTF-8 输出，宁可替换字符也不要崩。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老解释器没有 reconfigure
    pass


def load_snapshots(results_dir: Path) -> tuple[list[dict], list[str]]:
    """按文件名（时间戳前缀）排序读快照。损坏的文件跳过并告警，不崩。"""
    snapshots: list[dict] = []
    broken: list[str] = []
    if not results_dir.is_dir():
        return snapshots, broken
    for path in sorted(results_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:  # 半截写入 / 手工改坏都算损坏
            broken.append(f"{path.name}（{type(exc).__name__}）")
            continue
        if not isinstance(data, dict) or not isinstance(data.get("results"), list):
            broken.append(f"{path.name}（结构不符合 {{at, results[]}}）")
            continue
        snapshots.append({
            "file": path.name,
            "at": str(data.get("at") or path.stem),
            "cases": {c["id"]: c for c in data["results"]
                      if isinstance(c, dict) and c.get("id")},
        })
    return snapshots, broken


def case_metrics(case: dict) -> dict:
    """从一条用例记录算通过率与 token。数据全部来自已有快照，零新采集。"""
    runs = case.get("runs") or []
    total = int(case.get("total_runs") or len(runs) or 0)
    passed = int(case.get("passed_runs")
                 or sum(1 for r in runs if r.get("passed")))
    avg = (passed / total) if total else 0.0
    # review 轮次：判定器说不清（judge 未配置/失败/与正则不一致）。旧快照没有
    # verdict 字段，按 passed 反推——review 只会出现在 T5 之后的新快照里。
    reviewed = sum(1 for r in runs
                   if (r.get("verdict") or ("pass" if r.get("passed") else "fail")) == "review")
    tokens = 0
    for run in runs:
        for meta in run.get("turn_meta") or []:
            tokens += int(meta.get("completion_tokens") or 0)
            tokens += int(meta.get("retry_completion_tokens") or 0)
    return {
        "layer": str(case.get("layer") or "offline"),
        "xfail": bool(case.get("xfail")),
        "passed_runs": passed,
        "total_runs": total,
        "reviewed_runs": reviewed,
        "avg": avg,
        "all_pass": bool(total) and passed == total,
        # pass^k 估计：k 轮采样全部通过的概率。avg^0 定义为 1.0（无轮次时无从判断）。
        "pass_k": avg ** total if total else 1.0,
        "tokens": tokens,
        "failed_checks": [
            f"轮{r.get('run')} [{c.get('type')}:{c.get('target', 'reply')}] {c.get('detail', '')}"
            for r in runs if not r.get("passed")
            for c in (r.get("checks") or []) if not c.get("pass")
        ],
    }


def pct(x: float) -> str:
    return f"{x * 100:5.1f}%"


def render(snapshots: list[dict], broken: list[str], results_dir: Path) -> str:
    lines: list[str] = []
    lines.append("=== 评测报告（报告层，不计入门禁退出码） ===")
    lines.append(f"快照目录: {results_dir}（只读，不删不轮转）")
    lines.append(f"快照数量: {len(snapshots)} 份")
    for name in broken:
        lines.append(f"警告: 跳过损坏的快照 {name}")

    if not snapshots:
        lines.append("无历史数据：目录里没有可读的快照。")
        lines.append("（新克隆上 evals/results/ 被 .gitignore 忽略，本就是空的；"
                     "先跑 .\\.venv\\Scripts\\python.exe scripts\\run_evals.py 生成第一份。）")
        return "\n".join(lines)

    latest, previous = snapshots[-1], (snapshots[-2] if len(snapshots) > 1 else None)
    lines.append(f"最新快照: {latest['file']}（at={latest['at']}）")
    if previous:
        lines.append(f"上次快照: {previous['file']}（at={previous['at']}）")
    else:
        lines.append("上次快照: 无——仅一份快照，本次不出配对差异。")

    latest_cases = latest["cases"]
    layers: dict[str, int] = {}
    for case in latest_cases.values():
        layer = str(case.get("layer") or "offline")
        layers[layer] = layers.get(layer, 0) + 1
    lines.append("最新快照构成: " + " / ".join(
        f"{k} {v} 条" for k, v in sorted(layers.items()))
        + "（offline 运行只落 offline 用例，--live 运行才落 live 用例；跨层比较要看这一行）")

    lines.append("")
    lines.append("--- 通过率表（最新快照） ---")
    lines.append(f"{'用例':38} {'layer':7} {'轮通过':>6} {'平均':>7} "
                 f"{'全过概率':>8} {'review':>6} {'xfail':>5} {'tokens':>8}")
    metrics: dict[str, dict] = {}
    for case_id, case in latest_cases.items():
        m = case_metrics(case)
        metrics[case_id] = m
        lines.append(
            f"{case_id[:38]:38} {m['layer']:7} "
            f"{m['passed_runs']}/{m['total_runs']:<4} {pct(m['avg'])} "
            f"{pct(m['pass_k'])} {m['reviewed_runs']:>6} "
            f"{('xfail' if m['xfail'] else ''):>5} "
            f"{(m['tokens'] if m['tokens'] else '—'):>8}")
    lines.append("全过概率 = 平均通过率 ^ 轮数（pass^k 估计）："
                 "平均高而全过概率低 = 有能力但不稳定；两者都低 = 能力不足。")
    if any(m["reviewed_runs"] for m in metrics.values()):
        lines.append("review = 判定器说不清（judge 未配置 / 调用失败 / 与正则不一致），"
                     "既不计通过也不计失败，需人工复核落盘的原始回复。")

    failures = {cid: m["failed_checks"] for cid, m in metrics.items()
                if m["failed_checks"]}
    if failures:
        lines.append("")
        lines.append("--- 失败明细（最新快照，供排障；原始回复在快照的 runs[].turns） ---")
        for cid, details in failures.items():
            lines.append(f"  {cid}")
            for d in details[:6]:
                lines.append(f"      {d[:160]}")
            if len(details) > 6:
                lines.append(f"      …另有 {len(details) - 6} 条")

    lines.append("")
    lines.append("--- 与上次的配对差异 ---")
    if not previous:
        lines.append("  （无上次快照，跳过）")
    else:
        prev_cases = previous["cases"]
        for case_id, m in metrics.items():
            if case_id not in prev_cases:
                lines.append(f"  新   {case_id}  {pct(m['avg'])}")
                continue
            pm = case_metrics(prev_cases[case_id])
            if abs(pm["avg"] - m["avg"]) < 1e-9:
                mark = "="
            elif m["avg"] > pm["avg"]:
                mark = "↑"
            else:
                mark = "↓"
            lines.append(f"  {mark} {case_id:36} {pct(pm['avg'])} → {pct(m['avg'])}"
                         + (f"  （轮数 {pm['total_runs']}→{m['total_runs']}，"
                            "注意 run 数变化不是退化）"
                            if pm["total_runs"] != m["total_runs"] else ""))
        for case_id in prev_cases:
            if case_id not in metrics:
                lines.append(f"  缺   {case_id}（上次有、这次没有：可能是只跑了 offline 层）")

    lines.append("")
    lines.append("--- 成本（评测进程口径，不是生产 usage 口径） ---")
    total_tokens = sum(m["tokens"] for m in metrics.values())
    live_tokens = sum(m["tokens"] for m in metrics.values() if m["layer"] == "live")
    lines.append(f"  本次快照 completion_tokens 合计: {total_tokens}"
                 f"（其中 live {live_tokens}；含截断救援重试）")
    lines.append("  口径说明: 数字来自 run_evals.py 落盘的 turn_meta，"
                 "与生产记账（server/llm.py 的 last_usage、dispatcher 的 usage）"
                 "是两套，暂不合并，避免出现两个互不相等的成本数。")
    return "\n".join(lines)


def build_json(snapshots: list[dict]) -> dict:
    latest = snapshots[-1]
    return {
        "snapshot_count": len(snapshots),
        "latest": latest["file"],
        "latest_at": latest["at"],
        "previous": snapshots[-2]["file"] if len(snapshots) > 1 else None,
        "cases": [{"id": cid, **case_metrics(case)}
                  for cid, case in latest["cases"].items()],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="评测报告层（只读快照，不计入门禁）")
    parser.add_argument("--results-dir", default=str(DEFAULT_RESULTS_DIR),
                        help="快照目录，默认 evals/results/")
    parser.add_argument("--json", default="",
                        help="把机器可读的报告写到这个路径（可选）")
    args = parser.parse_args()

    snapshots, broken = load_snapshots(Path(args.results_dir))
    print(render(snapshots, broken, Path(args.results_dir)))

    if args.json:
        out = Path(args.json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(build_json(snapshots) if snapshots else
                                  {"snapshot_count": 0, "cases": []},
                                  ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\n机器可读报告已写入: {out}")

    # 报告层永不因内容失败：没有快照、只有一份快照、快照损坏都是正常状态。
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
