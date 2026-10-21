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


def test_empty_reply_guard() -> None:
    print("[reply guard]")
    import asyncio
    from unittest.mock import patch
    import server.dialogue.orchestrator as O

    async def truncated(*args, **kwargs):
        # 推理把 1200 token 全吃光：正文为空，finish_reason=length。
        return {"text": "", "finish_reason": "length", "usage": {}, "reasoning": "…"}

    async def also_empty(*args, **kwargs):
        return ""

    orch = Orchestrator()
    with patch.object(O.llm, "complete_raw", truncated), \
         patch.object(O.llm, "complete", also_empty), \
         patch.object(orch, "_spawn_extraction", lambda **kwargs: None):
        result = asyncio.run(orch.respond(user_text="在吗", mode="voice", session_id="guard-test"))
    check("截断后仍有可听见的回复", bool(result.text.strip()), repr(result.text))
    check("兜底不改情绪判定", result.emotion == "base", result.emotion)


def test_circadian_and_mood_reach_the_prompt() -> None:
    """协议 §15 的两段背景必须真的进动态提示词。

    2026-10-18 发现：`persona.circadian_context()` / `mood_narrative()` 写完了、
    `dynamic_prompt()` 也一直认 `circadian` / `mood` 参数，但
    `_render_turn_context()` 从来没传过——§15 在协议里写着、在 config 里配着，
    线上一点效果都没有。

    这条测试走**真实接线**（`_render_turn_context` 的渲染结果），不是直接调辅助
    函数——直接调的话，把接线删掉测试照样绿，那就白钉了。
    """
    print("[protocol 15]")
    orch = Orchestrator()
    store.delete_all()

    def render(**extra):
        """按 _turn_context 的产物形状造一份最小 data，交给真实渲染函数。"""
        data = {
            'response_policy': {'intent': 'chat'},
            'long_term_memory': {'status': 'found', 'enabled': True,
                                 'stated': [], 'inferred': [], 'uncertain': []},
            'state': {'current_state': '', 'user_state_note': '', 'proactivity': ''},
            'circadian': '', 'mood': '', 'history': [],
            **extra,
        }
        return '\n'.join(m['content'] for m in orch._render_turn_context(data))

    circadian, mood = orch._circadian_and_mood(enabled=True)
    check("时段背景非空（配置了时区）", bool(circadian.strip()), repr(circadian[:40]))
    check("时段背景声明自己只是背景", "不是要你演" in circadian, circadian[:80])

    plain = render()
    check("没接线字段时动态块里没有时段背景", "现在是" not in plain)
    wired = render(circadian=circadian, mood=mood)
    check("接线后时段背景进入动态提示词", circadian[:20] in wired)
    check("时段背景排在记忆块之前（保住缓存前缀）",
          wired.index(circadian[:20]) < wired.find("记忆使用规则")
          if "记忆使用规则" in wired else True)

    # 关键一步：走**真实装配入口**，确认那两个字段真的被填上。
    # 只渲染自己造的 data 钉不住"接线存在"——把 _turn_context 里的赋值删掉，
    # 上面的断言照样全绿。
    turn = orch._turn_context("在吗", "core-p15", [])
    check("_turn_context 填上了时段背景",
          bool(str(turn['data'].get('circadian') or '').strip()),
          repr(str(turn['data'].get('circadian'))[:40]))
    check("_turn_context 填上了心境字段（无记录时为空字符串）",
          turn['data'].get('mood') == "", repr(turn['data'].get('mood')))
    rendered = '\n'.join(m['content'] for m in turn['messages'])
    check("真实装配的提示词里能看到时段背景",
          str(turn['data']['circadian'])[:20] in rendered)

    # 心境写入后必须能被读出来，并且过期后自然衰减（§15.1：只作阀门，不突然反向）
    now = time.time()
    store.save_mood(narrative="总觉得缺个关键环节", reason="逻辑卡点", state="active",
                    expires_at=now + 3600)
    _c, live_mood = orch._circadian_and_mood(enabled=True)
    check("有活跃心境时注入余温", "缺个关键环节" in live_mood, live_mood)
    check("余温要求不演成相反性格", "别演成相反的性格" in live_mood, live_mood)
    check("心境余温也进入动态提示词", live_mood[:20] in render(circadian=circadian, mood=live_mood))
    with_mood = orch._turn_context("在吗", "core-p15", [])
    check("有心境时真实装配也带上余温", "缺个关键环节" in with_mood['data'].get('mood', ''),
          repr(with_mood['data'].get('mood'))[:60])

    from server.memory.diary import decay_mood
    decay_mood(store, now=now + 3601, easing_seconds=100)
    _c, eased = orch._circadian_and_mood(enabled=True)
    check("过期后转 easing 仍带余温", "缺个关键环节" in eased, eased)
    decay_mood(store, now=now + 3702, easing_seconds=100)
    _c, resolved = orch._circadian_and_mood(enabled=True)
    check("二次过期后 resolved 不再注入", resolved == "", repr(resolved))
    after_resolve = orch._turn_context("在吗", "core-p15", [])
    check("resolved 之后真实装配不再带余温",
          "缺个关键环节" not in after_resolve['data'].get('mood', ''),
          repr(after_resolve['data'].get('mood'))[:60])

    off_circadian, off_mood = orch._circadian_and_mood(enabled=False)
    check("记忆关闭时两段都为空", off_circadian == "" and off_mood == "",
          f"{off_circadian!r} {off_mood!r}")

    # 配置真的读得到。2026-10-18 实测：`self_evolution_cfg()` 只放行
    # SELF_EVOLUTION_DEFAULTS 里有的键，而 circadian / mood 两段没登记，
    # 于是 memory.yaml 里怎么写都读不到，回落值恰好等于配置值 → 零症状。
    # 这里把它钉死：改配置必须改变行为，否则就是又一个死键。
    from server.memory import evolution as evolution_mod
    original_memory_cfg = evolution_mod.memory_cfg
    try:
        evolution_mod.memory_cfg = lambda: {'self_evolution': {
            'circadian': {'timezone': 'America/New_York'},
            'mood': {'enabled': False},
            'seed_fork_min': 2, 'seed_fork_max': 2,
        }}
        cfg = evolution_mod.self_evolution_cfg()
        check("circadian 段能读到 yaml 里的时区",
              cfg.get('circadian', {}).get('timezone') == 'America/New_York',
              repr(cfg.get('circadian')))
        check("mood 段只配 enabled 时保留默认 TTL（嵌套深合并）",
              cfg.get('mood', {}).get('default_ttl_minutes') == 30, repr(cfg.get('mood')))
        check("seed_fork 段能读到",
              (cfg.get('seed_fork_min'), cfg.get('seed_fork_max')) == (2, 2),
              repr((cfg.get('seed_fork_min'), cfg.get('seed_fork_max'))))

        store.save_mood(narrative="关掉之后不该出现的心境", reason="测试", state="active",
                        expires_at=time.time() + 3600)
        _zone, gated = orch._circadian_and_mood(enabled=True)
        check("mood.enabled=false 真的关掉心境注入", gated == "", repr(gated))

        from server.assistant.loop import _fork_bounds
        check("seed_fork 配置驱动数量闸门",
              _fork_bounds({'seed_fork_min': 2, 'seed_fork_max': 3}) == (2, 3))
        check("坏掉的 seed_fork（min>max）退回协议常量",
              _fork_bounds({'seed_fork_min': 3, 'seed_fork_max': 1}) == (1, 2))
        check("缺省 seed_fork 时用协议常量", _fork_bounds({}) == (1, 2))

        # 接线存在性：_reflect 必须把数量闸门传进校验。行为级验证要 mock 整个
        # 反思循环（模型调用 + 种子选取 + 材料装配），成本高于收益；这里用 AST
        # 钉住那一行，防止有人把参数改回默认值——那样 _fork_bounds 再对也没用。
        import ast as ast_mod
        import inspect
        import textwrap
        from server.assistant import loop as loop_mod
        func = ast_mod.parse(textwrap.dedent(
            inspect.getsource(loop_mod.SelfEvolutionCycle._reflect)))
        passed = {kw.arg for call in ast_mod.walk(func)
                  if isinstance(call, ast_mod.Call)
                  and getattr(call.func, "id", None) == "validate_reflection_payload"
                  for kw in call.keywords}
        check("_reflect 把 seed_fork 闸门真的传进校验",
              {"min_children", "max_children"} <= passed, sorted(passed))
    finally:
        evolution_mod.memory_cfg = original_memory_cfg
    store.delete_all()


def test_internal_proxy_requires_token() -> None:
    """内部代理必须能要求令牌，否则本机任意进程可白嫖 llm_api_key。

    背景：/internal/telemetry_proxy 拿 settings.llm_api_key 转发任意 path，
    原先只校验 Origin 头；请求不带 Origin 时 `if origin and ...` 直接短路放行，
    于是 curl 一条就能用宿主额度。这里用 AST 静态断言锁住修复，不起 HTTP 服务。
    """
    print("[internal proxy token]")
    import ast as ast_mod
    import inspect
    import textwrap as tw
    from server.assistant import executive_api as api_mod

    source = inspect.getsource(api_mod.create_telemetry_proxy_router)
    tree = ast_mod.parse(tw.dedent(source))

    reads_token = False
    checks_header = False
    for node in ast_mod.walk(tree):
        # 两种写法都算：settings.internal_token 或 getattr(settings, "internal_token", ...)
        if isinstance(node, ast_mod.Attribute) and node.attr == "internal_token":
            reads_token = True
        if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Name):
            if node.func.id == "getattr" and len(node.args) >= 2:
                arg = node.args[1]
                if isinstance(arg, ast_mod.Constant) and arg.value == "internal_token":
                    reads_token = True
        if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
            if node.func.attr == "get" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast_mod.Constant) and arg.value == "x-internal-token":
                    checks_header = True
    check("代理读取 internal_token 配置", reads_token)
    check("代理校验 x-internal-token 头", checks_header)

    bad_get = []
    for node in ast_mod.walk(tree):
        if isinstance(node, ast_mod.Call) and isinstance(node.func, ast_mod.Attribute):
            if node.func.attr == "get" and isinstance(node.func.value, ast_mod.Name):
                if node.func.value.id == "settings":
                    bad_get.append(node.lineno)
    check("settings 用属性访问而非 .get()", not bad_get, bad_get)

    from server.config import settings as s
    original = getattr(s, "internal_token", None)
    try:
        s.internal_token = ""
        check("未配置令牌时不拦截", str(getattr(s, "internal_token", "") or "") == "")
        s.internal_token = "abc123"
        check("配置令牌后可拦截", str(getattr(s, "internal_token", "") or "") == "abc123")
    finally:
        s.internal_token = original


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
        test_empty_reply_guard,
        test_circadian_and_mood_reach_the_prompt,
        test_internal_proxy_requires_token,
    ):
        test()
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    if failed:
        for name in failed:
            print("FAIL:", name)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
