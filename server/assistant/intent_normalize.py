# -*- coding: utf-8 -*-
"""意图路由的输入归一化（B1，issue #42）。

为什么需要：语音是主通道，ASR 会吃掉助词、写错同音字。
实测（dogfood #40）：「帮我安排一个沙盒打样，生成并校验一个小文件吧」
被识别成「安排一个沙盒打样，生成并校验一个小文件吧」——少一个「帮我」，
旧判定整句落空，委托进普通对话。

这里只做**可观测的文本规范化**，不改写语义、不猜用户意图：
- 去掉开头的礼貌前缀（帮我/麻烦你/请/劳驾…）
- 去掉句尾语气词（吧/呀/啊/呢/哦/嘛…）
- 修正常见同音错字（小西→小溪）

归一化的结果必须能被打印出来对照原文（`normalize_with_trace`），
不做隐式改写——路由判错的现场要能复盘。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# 开头礼貌前缀。顺序从长到短，避免「麻烦你」只吃掉「麻烦」。
_POLITE_PREFIXES = (
    "麻烦你", "麻烦您", "麻烦", "劳驾", "有劳",
    "帮我", "帮我给", "帮忙", "请你", "请您", "请帮我", "请",
    "能不能帮我", "能不能", "可以帮我", "可不可以帮我", "可不可以",
    "你帮我", "你帮忙",
)

# 句尾语气词（可叠加，如「吧呀」）。只去句尾，不动句中。
_TAIL_PARTICLES = ("吧", "呀", "啊", "呢", "哦", "嘛", "啦", "哈", "咯", "嘞")

# ASR 常见同音/近音错字。只收「确定是错的」这一类，宁少勿多：
# 多修一处就多一个可能改错用户原话的地方。
_ASR_FIXES = (
    ("小西", "小溪"),
    ("晓溪", "小溪"),
)

# 标点与空白：统一收成正常句子，去掉句尾标点便于后续匹配。
_PUNCT_TAIL = re.compile(r"[。．.!！?？~～\s]+$")
_SPACES = re.compile(r"\s+")


@dataclass(frozen=True)
class Normalized:
    """归一化结果。`original` 永远保留原文，便于日志复盘。"""

    original: str
    text: str
    steps: tuple[str, ...]

    @property
    def changed(self) -> bool:
        return self.text != self.original


def _strip_polite_prefix(text: str) -> tuple[str, str]:
    for prefix in _POLITE_PREFIXES:
        if text.startswith(prefix):
            return text[len(prefix):].lstrip(), f"prefix:{prefix}"
    return text, ""


def _strip_tail_particles(text: str) -> tuple[str, str]:
    removed = ""
    while text:
        for particle in _TAIL_PARTICLES:
            if text.endswith(particle):
                text = text[: -len(particle)]
                removed = particle + removed
                break
        else:
            break
    return text, (f"tail:{removed}" if removed else "")


def normalize_with_trace(raw: str) -> Normalized:
    """归一化并记录每一步做了什么（可观测）。"""
    original = raw if isinstance(raw, str) else ""
    text = _SPACES.sub(" ", original).strip()
    steps: list[str] = []

    for wrong, right in _ASR_FIXES:
        if wrong in text:
            text = text.replace(wrong, right)
            steps.append(f"asr:{wrong}->{right}")

    text = _PUNCT_TAIL.sub("", text)

    # 前缀与语气词都可能叠两层（「麻烦你帮我整理吧」），最多剥三轮。
    for _ in range(3):
        text, prefix_step = _strip_polite_prefix(text)
        if prefix_step:
            steps.append(prefix_step)
        text, tail_step = _strip_tail_particles(text)
        if tail_step:
            steps.append(tail_step)
        text = _PUNCT_TAIL.sub("", text)
        if not prefix_step and not tail_step:
            break

    return Normalized(original=original, text=text, steps=tuple(steps))


def normalize(raw: str) -> str:
    """只要归一化后的文本。"""
    return normalize_with_trace(raw).text
