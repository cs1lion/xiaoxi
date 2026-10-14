"""把"记忆抽取"这一环单独拆出来看。

正常流程里，抽取发生在回复之后、存库之前，出问题很难定位。这个脚本把
每一步都摊开：模型原始输出 -> 解析结果 -> 实际入库 -> 事后召回。

    python scripts/inspect_extract.py                 # 跑内置样例
    python scripts/inspect_extract.py --text "我周五要面试"   # 单条试
    python scripts/inspect_extract.py --case 3 --full # 打印模型原文
    python scripts/inspect_extract.py --dry           # 只跑解析/入库/召回，不调模型

关键在于区分三种"没抽到"：
    1. 模型调用失败        -> trace.error 非空
    2. 模型说没什么好记的  -> 原始输出里 memories 是空数组
    3. 抽到了但被规则丢掉  -> dropped > 0
以前这三种都表现为"记忆是空的"，没法区分。
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.llm import llm  # noqa: E402
from server.memory.extractor import extract_memories, parse_extraction  # noqa: E402
from server.memory.manager import manager  # noqa: E402
from server.memory.store import store  # noqa: E402

CASES = [
    {
        "name": "明确说出的计划（该记）",
        "user": "下周五我要去面试一家做游戏引擎的公司",
        "assistant": "面试啊。紧张吗？",
        "expect_keys": ["面试"],
        "expect_min": 1,
    },
    {
        "name": "称呼偏好（该记，key 要稳定）",
        "user": "以后叫我老王就行，别叫名字",
        "assistant": "行，老王。",
        "expect_keys": ["称呼"],
        "expect_min": 1,
    },
    {
        "name": "一次情绪（不该记）",
        "user": "啊啊啊啊好烦啊！！",
        "assistant": "怎么了。",
        "expect_keys": [],
        "expect_min": 0,
        "expect_max": 0,
    },
    {
        "name": "寒暄（不该记）",
        "user": "在吗",
        "assistant": "在。干嘛。",
        "expect_keys": [],
        "expect_max": 0,
    },
    {
        "name": "纠正旧信息（应产生覆盖）",
        "user": "我早就不玩文明6了，太耗时间",
        "assistant": "那你现在玩什么？",
        "expect_keys": ["文明"],
        "expect_min": 1,
        "seed": [
            {"kind": "fact", "subject": "user", "key": "游戏:偏好", "value": "用户喜欢玩文明6", "importance": 0.7},
        ],
        "expect_supersede": True,
    },
    {
        "name": "推测 vs 明说（来源要分开）",
        "user": "唉，最近项目怎么老延期啊，算了不说了",
        "assistant": "听着挺烦的。",
        "expect_keys": [],
        "expect_min": 0,
    },
    {
        "name": "角色自己的承诺（subject=self）",
        "user": "你记得吗，上次说好一起看那个片子的",
        "assistant": "记得，你欠我两周了。",
        "expect_keys": ["看"],
        "expect_min": 1,
    },
    {
        "name": "长文本噪声（不该整段存）",
        "user": "我刚才那段话".replace("刚才", "在上一个小时的会议里讲了将近四十分钟关于渲染管线重构的进展，然后被问到 shadow map 的精度问题"),
        "assistant": "听着挺正式的。",
        "expect_keys": [],
        "expect_max": 1,
    },
]

# --dry 用的"假设模型这么返回"，用来验证规则层而不需要真模型
FAKE_PAYLOADS: dict[str, dict] = {
    "明确说出的计划（该记）": {
        "memories": [
            {"kind": "todo", "subject": "user", "key": "计划:周五面试",
             "value": "用户下周五要去面试一家做游戏引擎的公司", "source": "stated", "importance": 0.8},
        ]
    },
    "称呼偏好（该记，key 要稳定）": {
        "memories": [
            {"kind": "fact", "subject": "user", "key": "称呼",
             "value": "用户希望被叫作老王", "source": "stated", "importance": 0.9},
        ],
        "expect_key_prefix": "偏好:",
    },
    "一次情绪（不该记）": {
        "memories": [
            {"kind": "fact", "subject": "user", "key": "情绪:今天",
             "value": "用户今天很烦躁", "source": "inferred", "importance": 0.3},
            {"kind": "fact", "subject": "user", "key": "噪音:1",
             "value": "好的", "source": "stated", "importance": 0.1},
            {"kind": "fact", "subject": "user", "key": "噪音:2",
             "value": "在吗", "source": "stated", "importance": 0.6},
        ]
    },
    "寒暄（不该记）": {
        "memories": [
            {"kind": "episode", "subject": "user", "key": "寒暄",
             "value": "你好", "source": "stated", "importance": 0.6},
        ]
    },
    "纠正旧信息（应产生覆盖）": {
        "memories": [
            {"kind": "fact", "subject": "user", "key": "游戏:偏好",
             "value": "用户已经不再玩文明6了，觉得太耗时间", "source": "stated", "importance": 0.6},
        ]
    },
    "推测 vs 明说（来源要分开）": {
        "memories": [
            {"kind": "episode", "subject": "user", "key": "项目:延期",
             "value": "用户的项目最近经常延期，他自己似乎也不满意", "source": "inferred", "importance": 0.5},
        ]
    },
    "角色自己的承诺（subject=self）": {
        "memories": [
            {"kind": "episode", "subject": "user", "key": "约定:一起看电影",
             "value": "用户和角色约好一起看一个片子，已经拖了两周", "source": "stated", "importance": 0.6},
        ]
    },
    "长文本噪声（不该整段存）": {
        "memories": [
            {"kind": "episode", "subject": "user", "key": "工作:会议",
             "value": "x" * 200, "source": "stated", "importance": 0.4},
            {"kind": "fact", "subject": "user", "key": "工作:内容",
             "value": "用户开会讲了四十分钟渲染管线重构", "source": "stated", "importance": 0.4},
        ]
    },
}

for _case in CASES:
    _case["fake_payload"] = FAKE_PAYLOADS.get(_case["name"])


def dump(label: str, value: object) -> None:
    print(f"\n  {label}")
    if isinstance(value, (list, dict)) and not value:
        print("    (空)")
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, dict):
                bits = f"[{item.get('kind')}/{item.get('source')}] {item.get('key')} = {item.get('value')}"
                print(f"    {bits}  imp={item.get('importance')}")
            else:
                print(f"    {item}")
    elif isinstance(value, dict):
        for key, val in value.items():
            print(f"    {key}: {val}")
    else:
        print(f"    {value}")


async def run_case(case: dict, *, show_raw: bool, dry: bool) -> dict:
    print("=" * 72)
    print(f"■ {case['name']}")
    print(f"  用户：{case['user'][:60]}")
    print(f"  角色：{case['assistant'][:60]}")

    store.delete_all()
    for seed in case.get("seed", []):
        store.upsert(**seed)

    trace: dict = {}
    drops: list = []
    if dry:
        # 不调模型，但仍然过一遍"模型输出 -> 规则解析"这段路，
        # 这样离线也能验证 key 规范化、来源门槛、寒暄过滤、覆盖标记。
        payload = case.get("fake_payload")
        items = parse_extraction(payload, drops=drops) if payload is not None else []
        print("\n  [dry] 未调用模型，只走解析/入库/召回")
        dump("② 规则解析后（用内置样例输出）", items)
        if drops:
            dump("✗ 被规则丢弃", drops)
    else:
        items = await extract_memories(case["user"], case["assistant"], trace=trace)
        drops = trace.get("drops") or []

        if trace.get("error"):
            print(f"\n  ✗ 模型调用失败：{trace['error']}")
        dump("① 模型原始输出", trace.get("raw", ""))
        if show_raw:
            print("\n    原始 JSON:", json.dumps(trace.get("payload"), ensure_ascii=False)[:800])
        dump("② 规则解析后（要入库的候选）", items)
        if drops:
            dump("✗ 被规则丢弃（不是模型没抽到，是规则挡下的）", drops)
        print(f"    耗时 {trace.get('elapsed_ms')} ms")

    saved = []
    for item in items:
        memory = manager.remember(session_id="inspect", **item)
        if memory:
            saved.append(memory)

    dump("③ 实际入库（active）", [m.to_dict() for m in saved])
    if case.get("expect_supersede"):
        old = store.history_for_key("游戏:偏好")
        print(f"    历史里 {len(old)} 条，其中 superseded "
              f"{sum(1 for m in old if m.status == 'superseded')} 条")

    probe = case["user"][:10]
    dump("④ 事后召回（拿同一句话去检索）", manager.context_for(probe))

    problems = []
    if not dry:
        if trace.get("error"):
            problems.append("模型调用失败")
        expected_min = case.get("expect_min", 0)
        expected_max = case.get("expect_max", 99)
        if len(items) < expected_min:
            problems.append(f"抽少了（{len(items)} < {expected_min}）")
        if len(items) > expected_max:
            problems.append(f"抽多了（{len(items)} > {expected_max}）")
        for needle in case.get("expect_keys", []):
            if not any(needle in (i["key"] + i["value"]) for i in items):
                problems.append(f"没提到「{needle}」")
        prefix = case.get("expect_key_prefix")
        if prefix and not any(i["key"].startswith(prefix) for i in items):
            got = [i["key"] for i in items] or "(空)"
            problems.append(f"key 前缀应为 {prefix}，实际 {got}")
        if case.get("expect_supersede"):
            old = store.history_for_key("游戏:偏好")
            if sum(1 for m in old if m.status == "superseded") < 1:
                problems.append("旧记忆没有被标记为 superseded")

    print(f"\n  判定：{'PASS' if not problems else 'FAIL — ' + '；'.join(problems)}")
    store.delete_all()
    return {"name": case["name"], "ok": not problems, "problems": problems, "count": len(items)}


async def run_adhoc(text: str, reply: str, *, show_raw: bool) -> None:
    print("=" * 72)
    print(f"■ 单条试\n  用户：{text}\n  角色：{reply}")
    trace: dict = {}
    items = await extract_memories(text, reply, trace=trace)
    if trace.get("error"):
        print(f"\n  ✗ {trace['error']}")
    dump("① 原始", trace.get("raw", ""))
    dump("② 解析后", items)
    for item in items:
        memory = manager.remember(session_id="adhoc", **item)
        print(f"    入库 #{memory.id if memory else '?'} {item['key']}")
    dump("③ 召回", manager.context_for(text[:10]))
    store.delete_all()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default=None, help="只试一句话")
    parser.add_argument("--reply", default="嗯。", help="配合 --text 的角色回复")
    parser.add_argument("--case", type=int, default=None, help="只跑第 N 个样例")
    parser.add_argument("--full", action="store_true", help="打印模型原始 JSON")
    parser.add_argument("--dry", action="store_true", help="不调模型")
    args = parser.parse_args()

    if llm.offline:
        print("⚠ 没有配置 LLM_API_KEY，当前是离线 mock。")
        print("  mock 不理解中文，抽取结果无意义——只能用来验证 --dry 路径和规则解析。\n")

    if args.text:
        await run_adhoc(args.text, args.reply, show_raw=args.full)
        return 0

    selected = CASES if args.case is None else [CASES[args.case - 1]]
    results = [await run_case(c, show_raw=args.full, dry=args.dry) for c in selected]
    passed = sum(1 for r in results if r["ok"])
    print("=" * 72)
    print(f"=== {passed}/{len(results)} 通过 ===")
    for r in results:
        if not r["ok"]:
            print(f"  FAIL {r['name']}: {'；'.join(r['problems'])}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))