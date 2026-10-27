# -*- coding: utf-8 -*-
"""意图判定器在语料上的测量（B1，issue #42）。

**只测量、不改判据**：输出混淆矩阵与错误清单，为 B2 的阈值/判据调整提供基线。

作为 `run_gates` 的 `intent_corpus` 门禁，它还断言两条底线（见下方常量）：
真实语料准确率地板线、两份语料的差距上限。没有这两条，`run_gates` 报的
「跑过并断言」对它是空话——准确率掉到 60% 也退出 0。

    .\\.venv\\Scripts\\python.exe scripts\\measure_intent_corpus.py
    .\\.venv\\Scripts\\python.exe scripts\\measure_intent_corpus.py --verbose   # 列出全部错误
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DB_PATH", ":memory:")
os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("LLM_API_KEY", "")

CORPUS = ROOT / "evals" / "intent_corpus.jsonl"
# 用户真实语料：判据调整的**真实基准**。自造语料容易过拟合（实测同一判定器
# 在自造 90.3%、真实 80.8%），所以两份都要量、都要报。
CORPUS_USER = ROOT / "evals" / "intent_corpus_user.jsonl"

# 真实语料准确率地板线。文档既定纪律是「判据调整时以真实语料为准」——
# 既然以它为准，就必须对它设下限，否则回归无人把守。
# 取 0.80：2026-10-27 实测 85.0%，下方留 5pt 余量；能抓住真实退化，
# 又不因判据微调的正常波动误红。抬高地线前先确认不是语料本身变了。
REAL_ACCURACY_FLOOR = 0.80

# 两份语料准确率差距上限。脚本原有口径就是「差距 >5pt 说明判据正在拟合
# 自造语料」（见 main() 的打印），但那只是警告、从不产生退出码——过度拟合
# 因此可以静默发生。这里把它变成断言。当前实测 4.5pt，有余量；若有人只优化
# 自造语料而真实语料不动，差距涨过上限即红，那正是过度拟合的信号。
MAX_CORPUS_GAP = 0.05

# 判定器把一句话分到哪个「通道」。这里只对照语料的 label 与判定器的落点，
# 不评价每种通道内部实现。
CHANNEL_NAMES = {
    "delegate": "工具路径（委托）",
    "progress": "进度路由",
    "accept": "验收路由",
    "reject": "打回路由",
    "chat": "普通对话",
}


def load_corpus(path: Path = CORPUS) -> list[dict]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def route_of(text: str, service, orchestrator) -> str:
    """这句话会被分到哪条通道（按真实调用顺序：路由层 → 服务层）。

    委托有**两个**独立入口，任一命中都算落到委托通道：
    - `orchestrator.delegation_intent`：台前快环派给麾下 Worker（`_DELEGATION`）
    - `service.should_route`：幕后工具路径（AgentLoop），宽得多
    只测其中一个会得出失真的准确率（实测只测后者时 67 条委托被记为漏判）。
    """
    from server.dialogue.orchestrator import (acceptance_intent, delegation_intent,
                                              progress_query_intent)

    normalized = text
    try:
        from server.assistant.intent_normalize import normalize
        normalized = normalize(text)
    except Exception:
        pass

    # 验收/打回是整句形态，先判
    verdict = acceptance_intent(normalized)
    if verdict == "accept":
        return "accept"
    if verdict == "reject":
        return "reject"
    if progress_query_intent(normalized):
        return "progress"
    if delegation_intent(normalized) or service.should_route(normalized):
        return "delegate"
    return "chat"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    from server.assistant.service import AssistantService
    from server.dialogue.orchestrator import Orchestrator
    import tempfile

    service = AssistantService(Path(tempfile.mkdtemp(prefix="intent-measure-")), None, None)
    orchestrator = Orchestrator()

    results = {}
    for label, path in (("自造语料", CORPUS), ("真实语料", CORPUS_USER)):
        if not path.exists():
            continue
        rows = load_corpus(path)
        results[label] = measure(rows, service, orchestrator, verbose=args.verbose)
        print()

    if "真实语料" in results and "自造语料" in results:
        fake = results["自造语料"]["accuracy"]
        real = results["真实语料"]["accuracy"]
        gap = fake - real
        print(f"两份语料差 {gap:.1%}（自造 {fake:.1%} / 真实 {real:.1%}）"
              + ("——差距 >5pt 说明判据正在拟合自造语料，以真实语料为准"
                 if gap > 0.05 else "——差距在可接受范围"))

    report = ROOT / "eval" / "intent" / "corpus_baseline.json"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n基线写入 {report}")
    service.close()

    # 门禁断言（R22）：没有这几条，本脚本作为 run_gates 的 intent_corpus 门禁
    # 永远退出 0，「跑过并断言」对它是一句空话。失败信息一律不以 SKIP 开头
    # （run_gates 的 skip_regex 是行锚定，会被误判成「跳过」）。
    # R50：真实语料是权威输入（既定纪律：以真实语料为准）。它缺失时地板线与
    # 差距断言会双双跳过——旧代码在此分支打印「地板线仍生效」却实则一条断言
    # 都没跑、exit 0，门禁空转还撒谎。权威输入缺失必须 fail-closed，不静默绿。
    failures: list[str] = []
    if "真实语料" not in results:
        failures.append(
            f"真实语料缺失（{CORPUS_USER.name} 不存在或读不到）——既定断言以真实语料"
            "为权威输入，缺失即无法判定路由质量；该文件受 git 跟踪，若误删请恢复")
    else:
        real = results["真实语料"]["accuracy"]
        if real < REAL_ACCURACY_FLOOR:
            failures.append(
                f"真实语料准确率 {real:.1%} 低于地板线 {REAL_ACCURACY_FLOOR:.1%}"
                "——判据在真实分布上退化了（文档既定纪律：以真实语料为准）")
        if "自造语料" in results:
            gap = results["自造语料"]["accuracy"] - real
            if gap > MAX_CORPUS_GAP:
                failures.append(
                    f"两份语料差 {gap:.1%} 超过上限 {MAX_CORPUS_GAP:.1%}"
                    "——判据可能在拟合自造语料；请以真实语料为准复核判据")
        else:
            # 自造语料缺失只影响差距断言；地板线仍对真实语料生效（这句现在是真话）。
            print("自造语料缺失，跳过差距断言（地板线仍对真实语料生效）。")
    if failures:
        print("\n门禁失败：")
        for line in failures:
            print(f"  {line}")
        return 1
    return 0


def measure(rows: list[dict], service, orchestrator, *, verbose: bool) -> dict:
    """在一份语料上跑混淆矩阵，打印并返回结果。"""
    labels = ["delegate", "progress", "accept", "reject", "chat"]
    matrix = {expected: {got: 0 for got in labels} for expected in labels}
    errors: list[tuple[str, str, str, str]] = []

    for row in rows:
        expected = row["label"]
        got = route_of(row["text"], service, orchestrator)
        matrix[expected][got] += 1
        if got != expected:
            errors.append((row["text"], expected, got, row.get("note", "")))

    total = len(rows)
    correct = sum(matrix[label][label] for label in labels)
    print(f"语料 {total} 条，判对 {correct} 条（{correct / total:.1%}）\n")

    width = max(len(name) for name in CHANNEL_NAMES.values())
    print(f"{'期望 \\ 实际':<12}" + "".join(f"{CHANNEL_NAMES[l]:<{width + 2}}" for l in labels))
    for expected in labels:
        cells = "".join(f"{matrix[expected][got]:<{width + 2}}" for got in labels)
        print(f"{CHANNEL_NAMES[expected]:<12}" + cells)

    print(f"\n错误 {len(errors)} 条：")
    by_pair: dict[tuple[str, str], list[tuple[str, str, str, str]]] = {}
    for item in errors:
        by_pair.setdefault((item[1], item[2]), []).append(item)
    for (expected, got), items in sorted(by_pair.items(), key=lambda kv: -len(kv[1])):
        print(f"  {CHANNEL_NAMES[expected]} → {CHANNEL_NAMES[got]}：{len(items)} 条")
        show = items if verbose else items[:5]
        for text, _e, _g, note in show:
            print(f"    · {text}" + (f"   （{note}）" if note else ""))
        if not verbose and len(items) > 5:
            print(f"    … 另 {len(items) - 5} 条（--verbose 全列）")

    return {
        "total": total, "correct": correct, "accuracy": round(correct / total, 4),
        "matrix": matrix,
        "errors": [{"text": t, "expected": e, "got": g, "note": n} for t, e, g, n in errors],
    }


if __name__ == "__main__":
    raise SystemExit(main())
