# -*- coding: utf-8 -*-
"""生成意图路由校准语料（B1，issue #42）。

语料是**手写设计 + 程序化变体扩展**：每条种子句人工定标签与理由，
礼貌/语气/掉词变体由程序派生（这正是 ASR 会造成的差异）。
输出 `evals/intent_corpus.jsonl`，每行一条。

    .\.venv\Scripts\python.exe scripts/build_intent_corpus.py
    .\.venv\Scripts\python.exe scripts/build_intent_corpus.py --check   # 只校验现有文件
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "evals" / "intent_corpus.jsonl"

# ---------------------------------------------------------------------------
# 种子：(句子, 标签, target, 备注)
# 标签闭集：delegate / progress / accept / reject / chat
# target 闭集：file / research / writing / code / sandbox / other（非 delegate 留空）
# ---------------------------------------------------------------------------

SEEDS: list[tuple[str, str, str, str]] = [
    # ---- 标准礼貌式派活 ----
    ("帮我整理一下周报", "delegate", "writing", ""),
    ("麻烦你写一份会议纪要", "delegate", "writing", ""),
    ("帮我生成一个校验报告", "delegate", "file", ""),
    ("帮我安排一个沙盒打样", "delegate", "sandbox", ""),
    ("请把上周的讨论整理成要点", "delegate", "writing", ""),
    ("帮我写个脚本把这批文件重命名", "delegate", "code", ""),
    ("麻烦查一下这个库的文档", "delegate", "research", ""),
    ("帮我做个表格统计每天的用量", "delegate", "file", ""),
    ("请起草一封回复邮件", "delegate", "writing", ""),
    ("帮我研究一下这几个方案哪个更稳", "delegate", "research", ""),
    ("帮我跑一下测试看看有没有挂", "delegate", "code", ""),
    ("麻烦整理一下下载文件夹", "delegate", "file", ""),
    ("帮我把这段话翻译成英文", "delegate", "writing", ""),
    ("帮我看看这个报错是怎么来的", "delegate", "code", ""),
    ("帮我搜一下最近的会议记录", "delegate", "research", ""),
    ("请帮我把这单标记为完成", "delegate", "file", ""),
    ("帮我生成一份月度总结", "delegate", "writing", ""),
    ("麻烦把这几个文件按日期分类", "delegate", "file", ""),
    ("帮我写一个小工具做格式转换", "delegate", "code", ""),
    ("帮我查查这个接口的参数含义", "delegate", "research", ""),
    # ---- ASR 掉词式（去掉礼貌前缀）----
    ("整理一下周报", "delegate", "writing", "掉「帮我」"),
    ("写一份会议纪要", "delegate", "writing", "掉「麻烦你」"),
    ("生成一个校验报告", "delegate", "file", "掉「帮我」"),
    ("安排一个沙盒打样，生成并校验一个小文件吧", "delegate", "sandbox",
     "dogfood 实测原句：#40 的触发样本"),
    ("把上周的讨论整理成要点", "delegate", "writing", ""),
    ("写个脚本把这批文件重命名", "delegate", "code", ""),
    ("查一下这个库的文档", "delegate", "research", ""),
    ("做个表格统计每天的用量", "delegate", "file", ""),
    ("起草一封回复邮件", "delegate", "writing", ""),
    ("研究一下这几个方案哪个更稳", "delegate", "research", ""),
    ("跑一下测试看看有没有挂", "delegate", "code", ""),
    ("整理一下下载文件夹", "delegate", "file", ""),
    ("把这段话翻译成英文", "delegate", "writing", ""),
    ("看看这个报错是怎么来的", "delegate", "code", ""),
    ("搜一下最近的会议记录", "delegate", "research", ""),
    ("生成一份月度总结", "delegate", "writing", ""),
    ("把这几个文件按日期分类", "delegate", "file", ""),
    ("写一个小工具做格式转换", "delegate", "code", ""),
    ("查查这个接口的参数含义", "delegate", "research", ""),
    ("做个沙盒打样验证一下", "delegate", "sandbox", ""),
    # ---- 高危误判：句中提及但非派活 ----
    ("你觉得写代码累吗", "chat", "", "有「写」但是提问"),
    ("这家店的安排真不错", "chat", "", "有「安排」但是评价"),
    ("我最近在学写诗", "chat", "", "有「写」但是自述"),
    ("整理真的很难坚持", "chat", "", "有「整理」但是感慨"),
    ("帮我看看今天几号", "chat", "", "有「帮我」但只是问日期"),
    ("你能陪我聊会儿吗", "chat", "", "有「你能」但是求陪聊"),
    ("生成式模型现在进步好快", "chat", "", "有「生成」但是议论"),
    ("我上次查资料查到半夜", "chat", "", "有「查」「资料」但是回忆"),
    ("写周报这件事我一直很头疼", "chat", "", "有「写周报」但是抱怨"),
    ("脚本语言你觉得哪个好学", "chat", "", "有「脚本」但是询问"),
    ("他说安排一下就行", "chat", "", "转述别人的话"),
    ("报告写得挺好，就是太长了", "chat", "", "在评价已有报告"),
    ("文件我放桌面上啦", "chat", "", "告知，不是要求"),
    ("校验这个功能是谁做的", "chat", "", "询问责任人"),
    ("翻译软件现在挺准的", "chat", "", "评论工具"),
    ("研究报告我看完了", "chat", "", "告知进度"),
    ("表格样式我更喜欢简洁的", "chat", "", "表达偏好"),
    ("工具链老是出问题", "chat", "", "抱怨"),
    ("统计口径咱们之前定过吗", "chat", "", "询问历史决定"),
    ("分类这事不用太细", "chat", "", "给出意见而非派活"),
    # ---- 问句形式的派活 ----
    ("能不能帮我生成一个校验报告？", "delegate", "file", "问句形态"),
    ("你能整理下这个吗", "delegate", "writing", "问句形态"),
    ("可以帮我把这些文件合并吗", "delegate", "file", "问句形态"),
    ("能不能查一下这个库的版本", "delegate", "research", "问句形态"),
    ("你可以写个脚本试试吗", "delegate", "code", "问句形态"),
    ("方便帮我安排个打样吗", "delegate", "sandbox", "问句形态"),
    ("能帮我起草一下吗", "delegate", "writing", "问句形态"),
    ("可不可以帮我统计一下用量", "delegate", "file", "问句形态"),
    ("你能研究下哪个方案更好吗", "delegate", "research", "问句形态"),
    ("麻烦你跑一遍测试好吗", "delegate", "code", "问句形态"),
    # ---- 边界：英文混入 / 短句 / 口头修正 ----
    ("帮我 check 一下这个 PR", "delegate", "code", "英文混入"),
    ("写个 dockerfile", "delegate", "code", "英文混入"),
    ("帮我查 API 文档", "delegate", "research", "英文混入"),
    ("算了，不用了", "chat", "", "撤回请求"),
    ("不是，我是说帮我整理下笔记", "delegate", "writing", "口头修正后是派活"),
    ("等下，那个先别做", "chat", "", "叫停"),
    ("嗯……还是算了吧", "chat", "", "犹豫后放弃"),
    ("这个……你看着办", "chat", "", "模糊，不构成派活"),
    ("就那个", "chat", "", "指代不明"),
    ("帮我", "chat", "", "半句话，缺宾语"),
    # ---- 进度查询 ----
    ("那单进度怎么样了", "progress", "", ""),
    ("做完了吗", "progress", "", ""),
    ("还在跑吗", "progress", "", ""),
    ("什么时候能好", "progress", "", ""),
    ("有结论了吗", "progress", "", ""),
    ("交差了没", "progress", "", ""),
    ("刚才那个弄到哪一步了", "progress", "", ""),
    ("好了吗", "progress", "", ""),
    ("还在做吗", "progress", "", ""),
    ("那件事有进展吗", "progress", "", ""),
    # ---- 验收 ----
    ("收工", "accept", "", ""),
    ("可以了", "accept", "", ""),
    ("就这样吧", "accept", "", ""),
    ("没问题，收下", "accept", "", ""),
    ("这单结了", "accept", "", ""),
    ("不错，归档吧", "accept", "", ""),
    ("通过了", "accept", "", ""),
    ("挺好，留下吧", "accept", "", ""),
    ("行吧，成交", "accept", "", ""),
    ("存档吧", "accept", "", ""),
    # ---- 打回 ----
    ("不行，重做", "reject", "", ""),
    ("这个不对，再改一下", "reject", "", ""),
    ("打回", "reject", "", ""),
    ("顶栏那个按钮收回去", "reject", "", ""),
    ("漏了几条，补上", "reject", "", ""),
    ("重来一遍", "reject", "", ""),
    ("不满意，换个思路", "reject", "", ""),
    ("退回去改", "reject", "", ""),
    ("少了日期列", "reject", "", ""),
    ("不是这样，我指的是另一份", "reject", "", ""),
    # ---- 闲聊 ----
    ("早上好呀小溪", "chat", "", "dogfood 真实转录"),
    ("你还记得我的猫叫什么名字吗？", "chat", "", "记忆查询，非派活"),
    ("今天有点累", "chat", "", ""),
    ("陪我聊会儿天吧", "chat", "", ""),
    ("晚上吃什么好呢", "chat", "", ""),
    ("我今天遇到一件特别有意思的事", "chat", "", ""),
    ("外面下雨了", "chat", "", ""),
    ("我们刚才都聊了些什么？", "chat", "", "dogfood 真实转录"),
    ("谢谢你啊", "chat", "", ""),
    ("我有点睡不着", "chat", "", ""),
    ("你最近在忙什么", "chat", "", ""),
    ("明天要早起", "chat", "", ""),
    ("这首歌叫什么名字", "chat", "", ""),
    ("我买了新键盘", "chat", "", ""),
    ("周末想去爬山", "chat", "", ""),
    ("工作压力有点大", "chat", "", ""),
    ("你看过那部电影吗", "chat", "", ""),
    ("我养的多肉开花了", "chat", "", ""),
    ("好久没这么放松过了", "chat", "", ""),
    ("你会累吗", "chat", "", "问她感受，非派活"),
    # ---- 补充闲聊/边界（平衡标签分布：delegate 派生会放大，chat 必须够量）----
    ("你猜我刚才看到什么了", "chat", "", ""),
    ("这台电脑用了三年了", "chat", "", ""),
    ("我觉得最近记性变差了", "chat", "", ""),
    ("你平时都做什么呀", "chat", "", ""),
    ("我朋友推荐我看一本书", "chat", "", ""),
    ("今天地铁特别挤", "chat", "", ""),
    ("我想学点新东西但没想好学什么", "chat", "", ""),
    ("你说话声音挺好听的", "chat", "", ""),
    ("刚才那段音乐是什么", "chat", "", ""),
    ("我最近老做梦", "chat", "", ""),
    ("你说人为什么会拖延", "chat", "", "哲学提问"),
    ("这个房间采光不错", "chat", "", ""),
    ("我妈让我多穿点", "chat", "", ""),
    ("下雨天最适合睡觉了", "chat", "", ""),
    ("我刚泡了杯茶", "chat", "", ""),
    ("其实我不太喜欢热闹", "chat", "", ""),
    ("你会不会觉得无聊", "chat", "", ""),
    ("今天效率特别低", "chat", "", ""),
    ("我决定少刷手机了", "chat", "", ""),
    ("帮我记一下，我明天要买牛奶", "chat", "", "记一下=提醒，归提醒路径而非委托派活"),
    ("提醒我半小时后休息", "chat", "", "提醒类，走 reminder 而非委托"),
    ("晚点提醒我回消息", "chat", "", "提醒类"),
    ("你还记得我们上次聊到哪了吗", "chat", "", "记忆查询"),
    ("我昨天是不是说过要早睡", "chat", "", "记忆查询"),
    ("我最近总想吃辣的", "chat", "", ""),
    ("这个周末没安排", "chat", "", "有「安排」但是自述"),
    ("他答应帮我修的，结果忘了", "chat", "", "转述，非本次派活"),
    ("你说我该不该换个工作", "chat", "", "征求意见"),
    ("帮我个忙，别告诉别人", "chat", "", "有「帮我」但是请求保密，无操作对象"),
    ("我需要有人听我说说", "chat", "", ""),
    ("今天开会开了三个小时", "chat", "", ""),
    ("有点饿了", "chat", "", ""),
    ("你能理解那种感受吗", "chat", "", ""),
    ("我把钥匙放哪了来着", "chat", "", "自问，非要求检索文件"),
    ("这本小说的结局我不喜欢", "chat", "", ""),
    ("明天天气怎么样", "chat", "", "天气查询，非派活"),
    ("你帮我看看我是不是胖了", "chat", "", "玩笑式，无实际操作"),
    ("我刚才那句话是不是说重了", "chat", "", ""),
    ("感觉时间过得好快", "chat", "", ""),
    ("我先去洗漱了", "chat", "", "告知离开"),
    ("等我一下", "chat", "", ""),
    # ---- 补充边界：句中提及操作类词汇的闲聊 ----
    ("上次那个报告我还没看完", "chat", "", "有「报告」但是自述"),
    ("写东西的时候我喜欢听白噪音", "chat", "", "有「写」但是习惯描述"),
    ("整理房间对我来说很解压", "chat", "", "有「整理」但是感受"),
    ("这个脚本写得真漂亮", "chat", "", "评价已有代码"),
    ("统计数据的人辛苦了", "chat", "", "感慨"),
    ("归档的时候记得分类", "chat", "", "泛泛建议，不是要求她做"),
    ("生成图片的模型更新了", "chat", "", "资讯"),
    ("校验码是多少来着", "chat", "", "询问信息"),
    ("翻译腔太重了", "chat", "", "评价"),
    ("文件格式我改成 PDF 了", "chat", "", "告知已完成"),
]

# 程序化派生：礼貌/语气变体（模拟 ASR 前后缀差异，不改语义）
_VARIANTS = (
    ("", ""),
    ("帮我", ""),
    ("麻烦你", ""),
    ("", "吧"),
    ("帮我", "呀"),
)


def build_rows() -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()

    def add(text: str, label: str, target: str, note: str, source: str) -> None:
        text = text.strip()
        if not text or text in seen:
            return
        seen.add(text)
        row = {"text": text, "label": label, "source": source}
        if target:
            row["target"] = target
        if note:
            row["note"] = note
        rows.append(row)

    for text, label, target, note in SEEDS:
        add(text, label, target, note, "handwritten")

    # 只为前一半 delegate 种子派生变体：全量派生会把 delegate 放大五倍，
    # 标签严重失衡（实测 225 vs 46），阈值校准会被数量而非规律带偏。
    variants_budget = sum(1 for _t, label, _tg, _n in SEEDS if label == "delegate") // 2
    used = 0
    for text, label, target, note in SEEDS:
        if label != "delegate" or used >= variants_budget:
            continue
        used += 1
        for prefix, tail in _VARIANTS:
            if prefix and text.startswith(prefix):
                continue
            variant = f"{prefix}{text}{tail}"
            if variant == text:
                continue
            add(variant, label, target, note or "程序派生变体", "derived")

    return rows


def validate(rows: list[dict]) -> list[str]:
    problems: list[str] = []
    labels = {"delegate", "progress", "accept", "reject", "chat"}
    targets = {"file", "research", "writing", "code", "sandbox", "other"}
    for index, row in enumerate(rows, start=1):
        if set(row) - {"text", "label", "target", "source", "note"}:
            problems.append(f"#{index} 多余字段: {row}")
        if row.get("label") not in labels:
            problems.append(f"#{index} 标签越界: {row.get('label')!r}")
        if row.get("label") == "delegate" and row.get("target") not in targets:
            problems.append(f"#{index} delegate 缺合法 target: {row.get('target')!r}")
        if row.get("label") != "delegate" and row.get("target"):
            problems.append(f"#{index} 非 delegate 不该带 target")
        if not isinstance(row.get("text"), str) or not row["text"].strip():
            problems.append(f"#{index} 文本为空")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只校验现有文件，不重写")
    args = parser.parse_args()

    if args.check:
        if not OUT.exists():
            print(f"FAIL 语料文件不存在: {OUT}")
            return 1
        rows = [json.loads(line) for line in OUT.read_text(encoding="utf-8").splitlines() if line.strip()]
        problems = validate(rows)
        counts: dict[str, int] = {}
        for row in rows:
            counts[row["label"]] = counts.get(row["label"], 0) + 1
        print(f"共 {len(rows)} 条：{counts}")
        if problems:
            print("FAIL")
            for item in problems[:20]:
                print("  -", item)
            return 1
        print("PASS")
        return 0

    rows = build_rows()
    problems = validate(rows)
    if problems:
        print("FAIL 生成结果不合法")
        for item in problems[:20]:
            print("  -", item)
        return 1

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
                   encoding="utf-8")
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["label"]] = counts.get(row["label"], 0) + 1
    print(f"写入 {OUT}：{len(rows)} 条 {counts}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
