"""语料体检：检查 few-shot 和知识面的覆盖度是否均衡。

不是"越多越好"——是要能回答"哪些话题一条都没有"。
多样性研究（in-context learning 的 demo selection）里，
收益来自**话题散开**，尤其是 user 侧；只换回复不换提问，
模型学到的还是同一个分布。

用法：uv run python scripts/check_corpus.py
"""

from __future__ import annotations

import collections
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from server.config import persona_cfg  # noqa: E402

EMOTIONS = ("base", "调侃", "得意", "认真", "收敛", "轻微不服", "变小气")
# 期望覆盖的粗粒度话题面。任何一面为 0 都会让对话收缩到通用回答。
EXPECTED_AREAS = ("日常", "技术", "写作", "游戏", "影视", "情绪", "记忆", "自我", "状态", "边界")


def main() -> int:
    cfg = persona_cfg()
    shots = cfg.get("few_shot") or []
    knowledge = cfg.get("knowledge") or []
    problems: list[str] = []

    if len(shots) < 12:
        problems.append(f"few-shot 只有 {len(shots)} 条，定不住语气（建议 20~30）")

    # 情绪标签：格式 + 分布
    emotions = collections.Counter()
    for i, s in enumerate(shots):
        tag = re.match(r"^\[([^\]]+)\]", s.get("assistant", ""))
        if not tag:
            problems.append(f"few-shot 第 {i} 条没有 [情绪] 标签")
            continue
        name = tag.group(1)
        emotions[name] += 1
        if name not in EMOTIONS:
            problems.append(f"few-shot 第 {i} 条用了未知情绪 {name!r}")
    for name in EMOTIONS:
        if emotions[name] == 0:
            problems.append(f"情绪 {name!r} 在 few-shot 里一次都没出现，模型学不到")

    # 话题覆盖
    areas = collections.Counter()
    untagged = 0
    for s in shots:
        topic = s.get("topic") or ""
        if not topic:
            untagged += 1
            continue
        areas[topic.split("/")[0]] += 1
    if untagged:
        problems.append(f"{untagged} 条 few-shot 没有 topic 标签，无法检查覆盖度")

    # user 侧的多样性：如果提问句式高度重复，模型只会照着那一种模式接话
    openers = collections.Counter(s["user"][:4] for s in shots if s.get("user"))
    repeated = [k for k, v in openers.items() if v > 2]
    if repeated:
        problems.append(f"这些开头重复了 2 次以上，user 侧多样性不足：{repeated}")

    # 知识面
    if not knowledge:
        problems.append("没有 knowledge 段：模型不知道自己被允许聊什么，会收缩成通用回答")
    for k in knowledge:
        if not (k.get("stance") or "").strip():
            problems.append(f"知识面 {k.get('domain')!r} 只有话题没有立场，说出来还是泛泛而谈")

    print("few-shot 情绪分布: " + "  ".join(f"{k}={v}" for k, v in sorted(emotions.items())))
    print("话题面分布:       " + "  ".join(f"{k}={v}" for k, v in sorted(areas.items())))
    print(f"知识面: {len(knowledge)} 个领域：{'、'.join(k.get('domain', '') for k in knowledge)}")
    missing = [a for a in EXPECTED_AREAS if a not in areas]
    if missing:
        print(f"话题面缺口（可能有意如此，确认一下）：{'、'.join(missing)}")

    if problems:
        print("\nFAIL")
        for p in problems:
            print("  -", p)
        return 1
    print("\nPASS")
    return 0


def check_clipping() -> int:
    """裁剪必须永远守住上限，且只切在句边界上。

    这条以前没有测试，于是"至少保留一句"的规则能让单句超长时突破
    hard_limit——硬上限形同虚设。
    """
    from server.dialogue.orchestrator import clip_sentences

    cases = [
        ("这个嘛，我其实是这么看的。你要是非让我说，我也能编一段出来。", 20),
        ("第一句。第二句。第三句。第四句。", 12),
        ("一整句就超长度的情况而且没有标点", 8),
        ("刚好够。", 10),
        ("短。", 1),                      # 病态上限
        ("", 20),
        ("没有句号的一段很长很长很长很长很长的话", 10),
    ]
    problems: list[str] = []
    for text, limit in cases:
        out = clip_sentences(text, limit)
        if len(out) > max(limit, 2):
            problems.append(f"上限 {limit}：裁出 {len(out)} 字，超了 —— {out!r}")
        if text and not out:
            problems.append(f"上限 {limit}：非空输入被裁成空串")
        if "…" in out or "..." in out:
            problems.append(f"上限 {limit}：出现了省略号占位 —— {out!r}")

    if problems:
        print("\nFAIL 裁剪")
        for p in problems:
            print("  -", p)
        return 1
    print(f"裁剪检查通过（{len(cases)} 个用例，都守住了上限且不切在词中间）")
    return 0


if __name__ == "__main__":
    if main() == 0 and check_clipping() == 0:
        raise SystemExit(0)
    raise SystemExit(1)