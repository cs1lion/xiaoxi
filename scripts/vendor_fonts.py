"""把前端要用的网页字体抓到本地，生成一份可离线用的 fonts.css。

为什么要本地化：站点部署在自管 CVM 上，运行时依赖 jsDelivr / Google Fonts
在国内并不稳定；字体一挂，整个页面的排印层级就退回系统宋体。这里只做一次性
下载，产物落进 web/fonts/ 进版本库，运行期零外网请求。

字重与分片沿用 Fontsource 的做法：中文按 unicode-range 切成上百个小文件，
浏览器只会取页面真正用到的几片（首屏通常 5~15 片，约 100~300KB），
不会为了一个标题去下几 MB 的全量字库。

用法：  python scripts/vendor_fonts.py        （需要外网，只在换字体版本时跑）
"""

from __future__ import annotations

import re
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "web" / "fonts"

NOTO_CSS = "https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-serif-sc@5.3.1/index.css"
NOTO_BASE = "https://cdn.jsdelivr.net/npm/@fontsource-variable/noto-serif-sc@5.3.1/files/"
MONO = {
    # 小字眉标、图版编号、时刻这类信息用等宽；只取拉丁子集，单个 10KB 出头。
    "ibm-plex-mono-400.woff2": "https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.2.5/files/ibm-plex-mono-latin-400-normal.woff2",
    "ibm-plex-mono-500.woff2": "https://cdn.jsdelivr.net/npm/@fontsource/ibm-plex-mono@5.2.5/files/ibm-plex-mono-latin-500-normal.woff2",
}

FAMILY = "Noto Serif SC"
HEADER = """/* 本文件由 scripts/vendor_fonts.py 生成，不要手改。
 * Noto Serif SC（variable 200–900，SIL OFL 1.1）按 unicode-range 分片；
 * IBM Plex Mono（SIL OFL 1.1）只保留拉丁子集。
 * 运行期不再请求任何外部字体服务。 */
"""


def fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 font-vendor"})
    with urllib.request.urlopen(req, timeout=60) as res:
        return res.read()


def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    ns_dir = OUT / "noto-serif-sc"
    ns_dir.mkdir(parents=True, exist_ok=True)

    source = fetch(NOTO_CSS).decode("utf-8")
    blocks = re.findall(r"@font-face\s*\{[^}]*\}", source)
    if not blocks:
        print("没有解析到 @font-face，Fontsource 的 CSS 结构可能变了", file=sys.stderr)
        return 1

    total = 0
    out_blocks = []
    for block in blocks:
        match = re.search(r"url\(\./files/([^)]+)\)", block)
        if not match:
            continue
        name = match.group(1)
        target = ns_dir / name
        if not target.exists():
            data = fetch(NOTO_BASE + name)
            target.write_bytes(data)
        total += target.stat().st_size
        out_blocks.append(block.replace(f"./files/{name}", f"/static/fonts/noto-serif-sc/{name}")
                           .replace("'Noto Serif SC Variable'", f"'{FAMILY}'")
                           .replace("woff2-variations", "woff2"))

    mono_blocks = []
    for name, url in MONO.items():
        target = OUT / name
        if not target.exists():
            target.write_bytes(fetch(url))
        total += target.stat().st_size
        weight = "400" if "400" in name else "500"
        mono_blocks.append(
            "@font-face {\n"
            f"  font-family: 'IBM Plex Mono';\n  font-style: normal;\n  font-display: swap;\n"
            f"  font-weight: {weight};\n  src: url('/static/fonts/{name}') format('woff2');\n"
            "  unicode-range: U+0000-00FF,U+0131,U+0152-0153,U+02BB-02BC,U+02C6,U+02DA,U+02DC,"
            "U+0304,U+0308,U+0329,U+2000-206F,U+2074,U+20AC,U+2122,U+2191,U+2193,U+2212,"
            "U+2215,U+FEFF,U+FFFD;\n}")

    css = HEADER + "\n".join(out_blocks + mono_blocks) + "\n"
    (OUT / "fonts.css").write_text(css, encoding="utf-8")
    print(f"字体文件 {len(out_blocks)} 片 + 等宽 {len(mono_blocks)} 片，共 {total/1048576:.1f} MB")
    print(f"CSS -> {(OUT / 'fonts.css').relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
