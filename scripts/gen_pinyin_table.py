"""生成 web/avatar/pinyin_table.js —— 中文口型用的音节 -> viseme 查表。

只在需要重新生成时跑，不是运行时依赖：

    uv run --with pypinyin python scripts/gen_pinyin_table.py

做法：把 pypinyin 的 strict 声母/韵母在构建期就归约成 viseme 类别，
前端拿到的是 (char -> 声母类, 韵母类)，运行时不再需要任何拼音库。

编码：每个汉字 3 个码元 = 汉字本身 + 声母类码 + 韵母类码（均 base-36 单字符）。
"""

from __future__ import annotations

import pathlib

from pypinyin import Style, pinyin

ROOT = pathlib.Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "avatar" / "pinyin_table.js"

# --- viseme 类别：顺序即码值，改动会让旧表失效，必须同步重新生成 -------------
INITIAL_CLASSES = [
    "SIL",  # 0 零声母，直接进韵母
    "PP",   # 1 b p m  双唇
    "FF",   # 2 f      唇齿
    "DD",   # 3 d t n l 舌尖
    "KK",   # 4 g k h  舌根
    "SS",   # 5 z c s  齿龈擦音
    "NN",   # 6 zh ch sh r 舌尖后
    "CH",   # 7 j q x  舌面尖，咧口露齿
]

INITIAL_MAP = {
    "": 0,
    "b": 1, "p": 1, "m": 1,
    "f": 2,
    "d": 3, "t": 3, "n": 3, "l": 3,
    "g": 4, "k": 4, "h": 4,
    "z": 5, "c": 5, "s": 5,
    "zh": 6, "ch": 6, "sh": 6, "r": 6,
    "j": 7, "q": 7, "x": 7,
}

# 韵母保持 pypinyin 原样，运行时由 viseme.js 展开成口型序列。
# 这里只负责枚举，顺序必须与运行时 FINAL_LIST 一致。
FINAL_LIST = [
    "", "a", "ai", "an", "ang", "ao", "e", "ei", "en", "eng", "er",
    "i", "ia", "ian", "iang", "iao", "ie", "in", "ing", "iong", "iou",
    "o", "ong", "ou", "u", "ua", "uai", "uan", "uang", "uei", "uen",
    "ueng", "uo", "v", "van", "ve", "vn",
]

ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"


def code(value: int) -> str:
    if value >= len(ALPHABET):
        raise ValueError(f"code {value} out of range")
    return ALPHABET[value]


def main() -> None:
    rows: list[str] = []
    skipped = 0
    for point in range(0x4E00, 0xA000):
        char = chr(point)
        initials = pinyin(char, style=Style.INITIALS, strict=True, errors=lambda _: None)
        finals = pinyin(char, style=Style.FINALS, strict=True, errors=lambda _: None)
        if not initials or not finals or not finals[0]:
            skipped += 1
            continue
        initial = initials[0][0] or ""
        final = finals[0][0] or ""
        if initial not in INITIAL_MAP or final not in FINAL_LIST:
            skipped += 1
            continue
        rows.append(char + code(INITIAL_MAP[initial]) + code(FINAL_LIST.index(final)))

    body = "".join(rows)
    text = (
        "/* 由 scripts/gen_pinyin_table.py 生成，请勿手改。\n"
        f"   {len(rows)} 个汉字，覆盖 CJK 基本区；未收录的字回落到中性口型。\n"
        "   编码：每字 3 码元 = 汉字 + 声母类(base62) + 韵母索引(base62) */\n"
        "\n"
        f"export const INITIAL_CLASS_COUNT = {len(INITIAL_CLASSES)};\n"
        f"export const FINAL_COUNT = {len(FINAL_LIST)};\n"
        f"export const TABLE = \"{body}\";\n"
    )
    OUT.write_text(text, encoding="utf-8")
    size_kb = OUT.stat().st_size / 1024
    print(f"wrote {OUT} ({len(rows)} chars, skipped {skipped}, {size_kb:.1f} KB)")


if __name__ == "__main__":
    main()