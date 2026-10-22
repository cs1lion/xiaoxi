"""契约范围内的路径判定（与 server/contract/lint.py:143-157 同款）。

「契约范围内」= 工作区相对路径：空串视为未填写（返回空串表示没问题），
绝对路径 / 盘符 / 单个前导斜杠、以及含 .. 路径段的上跳都在范围外。

纯字符串判定，不碰文件系统。
"""
from __future__ import annotations

import re

# 与 lint.py 的 _ABS_PATH / _TRAVERSAL 逐字一致
_ABS_PATH = re.compile(r'^(?:[A-Za-z]:[\\/]|[\\/]{1,2}[^\\/])')
_TRAVERSAL = re.compile(r'(?:^|[\\/])\.\.(?:[\\/]|$)')


def path_in_contract_scope(path: str) -> str:
    """返回空串表示在范围内，否则返回中文原因。"""
    text = (path or "").strip()
    if not text:
        return ""
    if _ABS_PATH.match(text):
        return f"绝对路径不在契约范围内：{text!r}"
    if _TRAVERSAL.search(text):
        return f"路径含 .. 上跳，可能在契约范围外：{text!r}"
    return ""
