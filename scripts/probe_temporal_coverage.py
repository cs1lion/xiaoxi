"""Offline diagnostic: how much of the natural range-marker phrasing does the
conservative valid-time parser actually capture?

This is a measurement probe, not a gate: it records gaps and never fails the
build (same contract as scripts/probe_memory_contract.py). The conservative
parser deliberately leaves open-ended and ambiguous expressions unresolved --
that is the accuracy floor. What was never measured is the CEILING: of the
phrasings a user would realistically use to state an explicit bound, how many
does it actually recognise?

Run: .venv/Scripts/python.exe -B scripts/probe_temporal_coverage.py [--json PATH]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ['DB_PATH'] = ':memory:'
os.environ['LLM_PROVIDER'] = 'mock'
os.environ['LLM_API_KEY'] = ''

with patch('dotenv.load_dotenv', return_value=False):
    from server.memory.temporal import temporal_bounds

# A fixed clock so relative periods resolve deterministically.
NOW = datetime(2026, 6, 15, 12, 0, 0, tzinfo=timezone.utc).timestamp()

# Phrasings that state an EXPLICIT bound. Each should yield at least one bound.
# Grouped by the marker form so a miss points at the shape to consider.
SHOULD_CAPTURE: dict[str, list[str]] = {
    "从X起": [
        "从2024年3月起我在学Python",
        "从2024年3月15日起开始记账",
        "从2020年起住在这里",
        "从去年开始我在学Python",
        "从今年起规律作息",
    ],
    "自从X": [
        "自从2020年起住在这里",
        "自从去年开始在备考",
        "自从上个月起每天跑步",
    ],
    "到X为止": [
        "到去年为止我在甲公司工作",
        "到2024年6月为止住在这里",
        "到上个月为止在备考",
        "到今年为止还在原来岗位",
    ],
    "直到X": [
        "直到去年我都住在这里",
        "直到上个月还在用旧手机",
        "直到2024年12月都在甲公司",
    ],
    "从X到Y": [
        "从2024年1月到2024年12月住这里",
        "从2024年3月到6月在做那个项目",
        "从去年到今年一直在备考",
    ],
}

# Open-ended / ambiguous phrasings. NONE of these may produce a bound: a
# fabricated deadline is the accuracy failure this parser exists to prevent.
MUST_NOT_CAPTURE: dict[str, list[str]] = {
    "开放式/歧义": [
        "最近我在学Python",
        "这段时间很忙",
        "目前住在北京",
        "现在在加班",
        "前段时间换了工作",
        "一直以来都喜欢咖啡",
        "从小就不吃香菜",
        "平时习惯早睡",
        "以后想换个城市",
        "之前在一家小公司",
    ],
    "非时间「从/到」": [
        "从公司到家只要十分钟",
        "回到家已经十点了",
        "说到做到",
        "我从现在开始努力",
        "直到成功为止再睡",
        "等到了今年终于",
        "到今天我还记得他",
    ],
    # Discourse markers: 起/开始 must follow the date directly, so 从X说起/讲起
    # stays out. This group guards exactly the feature the 从X起 change brags
    # about -- without it a regression would still print 0 over-capture.
    # NOTE: 从X开始说起 is deliberately NOT here: it is an accepted residual
    # (see temporal.py's _FROM comment), so listing it would make this probe
    # report a permanent false red.
    "话语标记": [
        "从去年说起我经历过很多事",
        "从去年讲起都是回忆",
        "从2024年3月说起来那时候",
    ],
}


def run() -> dict:
    """Measure, print, and return the same numbers (so --json is not a lie)."""
    captured, missed = [], []
    for form, phrases in SHOULD_CAPTURE.items():
        for phrase in phrases:
            valid_from, valid_until = temporal_bounds(phrase, now=NOW)
            row = {"form": form, "phrase": phrase,
                   "valid_from": valid_from, "valid_until": valid_until}
            (captured if (valid_from is not None or valid_until is not None)
             else missed).append(row)

    over_captured = []
    for form, phrases in MUST_NOT_CAPTURE.items():
        for phrase in phrases:
            valid_from, valid_until = temporal_bounds(phrase, now=NOW)
            if valid_from is not None or valid_until is not None:
                over_captured.append({"form": form, "phrase": phrase,
                                      "valid_from": valid_from,
                                      "valid_until": valid_until})

    total = sum(len(phrases) for phrases in SHOULD_CAPTURE.values())
    guarded = sum(len(phrases) for phrases in MUST_NOT_CAPTURE.values())

    print(f"应捕获 {len(captured)}/{total}（{100 * len(captured) // max(1, total)}%）")
    if missed:
        print("\n未捕获（保守下限，逐条列出标记形态供判断是否过保守）：")
        for row in missed:
            print(f"  [{row['form']}] {row['phrase']}")
    print(f"\n误捕获 {len(over_captured)}/{guarded}"
          f"（必须为 0：错设界会让检索门误排除事实）")
    for row in over_captured:
        print(f"  !! [{row['form']}] {row['phrase']} -> "
              f"from={row['valid_from']} until={row['valid_until']}")

    by_form = {}
    for form, phrases in SHOULD_CAPTURE.items():
        hit = sum(1 for p in phrases
                  if temporal_bounds(p, now=NOW) != (None, None))
        by_form[form] = {"captured": hit, "total": len(phrases)}
    print("\n按标记形态：" + " · ".join(
        f"{k} {v['captured']}/{v['total']}" for k, v in by_form.items()))
    return {"now": NOW, "should_capture": {"captured": len(captured), "total": total,
                                           "missed": missed},
            "must_not_capture": {"over_captured": len(over_captured), "total": guarded,
                                 "violations": over_captured},
            "by_form": by_form}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--json", default="", help="把完整实测结果写到这个 JSON 路径")
    args = parser.parse_args(argv)
    report = run()
    if args.json:
        Path(args.json).write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                   encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
