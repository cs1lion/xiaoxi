"""场景回归测试：把需求草案里的场景变成可重复执行的检查。

用法：
    python scripts/eval_scenarios.py            # 用当前 LLM 配置跑全部场景
    python scripts/eval_scenarios.py --dry-run  # 只检查离线可验证的部分
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.dialogue.orchestrator import orchestrator  # noqa: E402
from server.memory.manager import manager  # noqa: E402
from server.memory.store import store  # noqa: E402

SCENARIOS = json.loads((ROOT / "eval" / "scenarios.json").read_text(encoding="utf-8"))["scenarios"]


def seed(memory: dict[str, Any]) -> int:
    return store.upsert(**memory)


def check(scenario: dict[str, Any], text: str) -> tuple[bool, list[str]]:
    rules = scenario.get("checks", {})
    problems: list[str] = []
    if "max_len" in rules and len(text) > rules["max_len"]:
        problems.append(f"过长 {len(text)}>{rules['max_len']}")
    questions = text.count("？") + text.count("?")
    if "max_questions" in rules and questions > rules["max_questions"]:
        problems.append(f"问句过多 {questions}>{rules['max_questions']}")
    for pattern in rules.get("forbid_regex", []):
        if re.search(pattern, text):
            problems.append(f"命中禁止内容 /{pattern}/")
    for pattern in rules.get("require_regex", []):
        if not re.search(pattern, text):
            problems.append(f"缺少预期内容 /{pattern}/")
    return (not problems), problems


async def run_scenario(scenario: dict[str, Any], verbose: bool) -> dict[str, Any]:
    store.delete_all()
    session_id = uuid.uuid4().hex[:10]
    store.start_session(session_id)

    for memory in scenario.get("seed_memory", []):
        seed(memory)
    if scenario.get("delete_before_reply"):
        for memory in scenario.get("seed_memory", []):
            written = store.list_memories(limit=50)
            for row in written:
                if row.key == memory["key"]:
                    manager.forget(row.id)

    result = await orchestrator.respond(session_id=session_id, user_text=scenario["user"])
    await orchestrator.drain()  # 抽取在后台跑，不等它就没有记忆可验
    ok, problems = check(scenario, result.text)

    after = scenario.get("checks", {}).get("expect_memory_after")
    if after:
        # key 会随归一化变化（游戏:偏好 -> 游戏:文明6），所以按主题和状态查，
        # 不按字面 key 查——那是在测实现细节，不是在测行为。
        topic = after["key"].split(":", 1)[0]
        active = [m for m in manager.snapshot() if m["key"].startswith(topic + ":")]
        if not active:
            ok, problems = False, problems + [f"没有主题为「{topic}」的活跃记忆"]
        elif not any(
            any(token in row["value"] for token in after["contains"]) for row in active
        ):
            ok, problems = False, problems + [f"记忆未纠正: {[r['value'] for r in active]}"]
        if after.get("old_key_superseded"):
            old = [m for m in store.list_memories(include_history=True)
                   if m.key == after["old_key_superseded"]]
            if not old or old[0].status != "superseded":
                ok, problems = False, problems + ["旧记忆没有被标记为 superseded"]

    if verbose:
        print(f"\n[{scenario['id']}] {scenario['name']}")
        print(f"  用户: {scenario['user']}")
        print(f"  角色: {result.text}")
        print(f"  判定: {'PASS' if ok else 'FAIL ' + '; '.join(problems)}")
    store.delete_all()
    return {"id": scenario["id"], "name": scenario["name"], "ok": ok, "problems": problems, "reply": result.text}


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--only", default=None, help="只跑某个 id，例如 S2")
    args = parser.parse_args()

    selected = [s for s in SCENARIOS if not args.only or s["id"] == args.only]
    if args.dry_run:
        for scenario in selected:
            seed_ok = all(check(scenario, "")[0] for _ in [0])
            print(f"[{scenario['id']}] {scenario['name']}: 规则已加载 ({len(scenario.get('checks', {}))} 项检查)")
        return 0

    results = [await run_scenario(s, True) for s in selected]
    passed = sum(1 for r in results if r["ok"])
    print(f"\n=== {passed}/{len(results)} 通过 ===")
    for result in results:
        if not result["ok"]:
            print(f"FAIL {result['id']} {result['name']}: {'; '.join(result['problems'])}")
    (ROOT / "eval" / "last_run.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))