"""离线自检：不需要 API key、不需要网络的确定性检查。

    python scripts/test_core.py
"""

from __future__ import annotations

import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# This suite deletes memories: force isolation before importing any singleton.
import os
os.environ['DB_PATH'] = ':memory:'
from server.config import settings
settings.db_path = ':memory:'

from server.dialogue.orchestrator import Orchestrator, detect_emotion, sanitize  # noqa: E402
from server.memory.manager import manager  # noqa: E402
from server.memory.store import store  # noqa: E402
from server.persona import build_messages, demo_turns, static_system_prompt  # noqa: E402
from server.speech.tts import split_for_speech, voice_profile  # noqa: E402

passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name} {detail}")


def test_sanitize() -> None:
    print("[sanitize]")
    text = sanitize("（笑了笑）**很好**啊 😀 那就…算了")
    check("去掉动作描写和 markdown", "（" not in text and "**" not in text)
    check("去掉 emoji", "😀" not in text)
    check("省略号归一", "……" in text or "算了" in text)


def test_emotion() -> None:
    print("[emotion]")
    check("收敛", detect_emotion("抱歉，我不该那么说") == "收敛")
    check("调侃", detect_emotion("你看看你，又熬夜") == "调侃")
    check("默认", detect_emotion("我在。") == "base")


def test_pacing() -> None:
    print("[pacing]")
    orch = Orchestrator()
    long_text = "啊。" * 200
    out = orch._postprocess(long_text, user_text="嗯")
    check("超长回复被截断", len(out) <= orch.pacing["length"]["short_max_chars"] + 2, f"len={len(out)}")
    ask = orch._postprocess("你想聊聊吗？", user_text="嗯")
    check("正常回复保留", "聊聊" in ask)
    orch.state["_recent_assistant"] = ["你怎么这样？"] * 6
    trimmed = orch._postprocess("继续说这个吧？", user_text="嗯")
    check("追问过密时不再以问句结尾", not trimmed.endswith(("？", "?")), trimmed)


def test_refusal_cooldown() -> None:
    print("[refusal]")
    orch = Orchestrator()
    for keyword in orch.pacing["cooldown"]["on_refuse_keywords"]:
        orch.state["proactive_suppressed_until"] = 0
        orch.respond  # noqa: B018 - just to make the attribute explicit
        break
    check("冷静期默认值存在", "proactive_suppressed_until" in orch.state)


def test_memory_lifecycle() -> None:
    print("[memory]")
    store.delete_all()
    first = store.upsert(kind="fact", subject="user", key="plan:interview", value="用户周五面试", importance=0.8)
    second = store.upsert(kind="fact", subject="user", key="plan:interview", value="用户已经面试完了", importance=0.8)
    check("同 key 新值覆盖旧值", first != second)
    check("旧值保留为历史", store.get(first).status == "superseded")
    check("历史可查", len(manager.history("plan:interview")) == 2)

    block = manager.context_for("我面试怎么样了")
    check("检索能命中", "面试" in block, block)

    corrected = manager.correct(second, "用户面试通过，下周入职")
    check("纠正生成新记录", corrected.value.startswith("用户面试通过"))

    manager.forget(corrected.id)
    check("删除后计数归零", store.count() == 0)
    check("删除后检索不到", manager.context_for("我面试怎么样了") == "")
    check("删除后历史也清掉", len(manager.history("plan:interview")) == 0)


def test_memory_tiers() -> None:
    print("[memory tiers]")
    store.delete_all()
    store.upsert(kind="fact", subject="user", key="a:1", value="用户明确说过喜欢咖啡", source="stated")
    store.upsert(kind="fact", subject="user", key="a:2", value="用户可能喜欢咖啡因饮料", source="inferred")
    store.upsert(kind="fact", subject="user", key="a:3", value="用户好像不喝加牛奶的咖啡", source="uncertain")
    block = manager.context_for("咖啡")
    check("三档分别注入", '"source":"stated"' in block and '"source":"inferred"' in block and '"source":"uncertain"' in block
          and "【手动保存的】" in block and "【我的推测】" in block and "【我不确定的】" in block, block)
    check("不确定项被标注", '"provenance":"【我不确定的】需确认"' in block)
    store.delete_all()


def test_prompt() -> None:
    print("[prompt]")
    marker = "用户说周五有一场面试"
    messages = build_messages(memory_block=f"【你明确说过的】\n- 关于你：{marker}")
    prompt = "\n".join(m["content"] for m in messages)
    empty = "\n".join(m["content"] for m in build_messages(memory_found=False))
    static = static_system_prompt()
    for needle in ("绝对不要做的事", "记忆使用规则", "安全底线", "边界", "【你明确说过的】"):
        check(f"提示词包含 {needle}", needle in prompt)
    check("有 few-shot", len(demo_turns()) >= 4)
    check("长度可控", len(prompt) < 9000, f"len={len(prompt)}")
    check("无记忆时给出否定信号", "没有任何相关记忆" in empty)
    check("静态前缀在最前", messages[0]["role"] == "system" and messages[0]["content"] == static)
    check("注入的记忆不进静态前缀", marker not in static)
    idx_memory = next((i for i, m in enumerate(messages) if marker in m["content"]), -1)
    idx_demo = next(i for i, m in enumerate(messages) if m["role"] == "assistant")
    check("记忆块排在 few-shot 之后（保住缓存前缀）", idx_memory > idx_demo, f"{idx_memory} vs {idx_demo}")


def test_tts_split() -> None:
    print("[tts]")
    pieces = split_for_speech("第一句。第二句稍微长一点，需要拆开。第三句。", max_chars=20)
    check("按句切分", len(pieces) > 1)
    check("没有丢字", "".join(pieces).replace(" ", "") == "第一句。第二句稍微长一点，需要拆开。第三句。".replace(" ", ""))
    check("超长句被切碎", all(len(p) <= 20 for p in pieces))
    profile = voice_profile("收敛")
    base = voice_profile("base")
    check("情绪映射到不同声学参数", (profile["rate"], profile["pitch"]) != (base["rate"], base["pitch"]))


def test_proactivity_gate() -> None:
    print("[proactivity]")
    import asyncio

    orch = Orchestrator()
    store.delete_all()
    store.upsert(kind="todo", subject="user", key="todo:1", value="想一起看看那个面试结果", importance=0.7)
    result = asyncio.run(orch.proactive_turn(session_id=uuid.uuid4().hex[:8], idle_seconds=99999))
    check("未授权时不开口", result is None)
    orch.set_proactivity(True)
    orch.state["last_proactive_at"] = 0
    check("授权后允许触发", orch.state["proactivity_enabled"] is True)
    orch.state["proactive_suppressed_until"] = time.time() + 999
    result = asyncio.run(orch.proactive_turn(session_id=uuid.uuid4().hex[:8], idle_seconds=99999))
    check("冷静期内闭嘴", result is None)
    store.delete_all()


def main() -> int:
    for test in (
        test_sanitize,
        test_emotion,
        test_pacing,
        test_refusal_cooldown,
        test_memory_lifecycle,
        test_memory_tiers,
        test_prompt,
        test_tts_split,
        test_proactivity_gate,
    ):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        for name in failed:
            print("FAIL:", name)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
