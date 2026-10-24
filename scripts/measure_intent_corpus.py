# -*- coding: utf-8 -*-
"""意图判定器在语料上的测量（B1，issue #42）。

**只测量、不改判据**：输出混淆矩阵与错误清单，为 B2 的阈值/判据调整提供基线。

    .\.venv\Scripts\python.exe scripts\measure_intent_corpus.py
    .\.venv\Scripts\python.exe scripts\measure_intent_corpus.py --verbose   # 列出全部错误
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
