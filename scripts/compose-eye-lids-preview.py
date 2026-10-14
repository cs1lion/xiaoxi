"""Compose a fixed-crop preview from a completed V3 eyelid calibration run."""
import argparse
import hashlib
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--font", default="C:/Windows/Fonts/msyh.ttc")
    args = parser.parse_args()
    run = args.run_dir.resolve()
    panels = [
        ("baseline-happy", "上一版预设 · 开心"),
        ("candidate-happy", "独立下睑与笑眼 · 开心"),
        ("lower-left-100", "仅画面左侧下睑 · 满强度"),
        ("baseline-shy", "上一版预设 · 害羞"),
        ("candidate-shy", "独立下睑与笑眼 · 害羞"),
        ("lid-max-stress-blink-100", "最大组合 · 完整眨眼"),
    ]
    crop = (170, 210, 555, 550)
    fonts = {size: ImageFont.truetype(args.font, size) for size in (17, 20, 29)}
    canvas = Image.new("RGB", (1032, 796), "#101722")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "细腻笑眼 · 实际渲染对照", font=fonts[29], fill="#e6edf7")
    draw.text((24, 62), "固定模型、相机、灯光与裁切；比较上一版预设与新增眼睑通道", font=fonts[17], fill="#a9bad0")
    sources = []
    for i, (capture_id, label) in enumerate(panels):
        path = run / "captures" / f"{capture_id}.png"
        with Image.open(path) as capture:
            if capture.size != (720, 720):
                raise ValueError(f"Expected frozen 720px capture: {path}")
            face = capture.convert("RGB").crop(crop).resize((316, 279), Image.Resampling.LANCZOS)
        x, y = 24 + i % 3 * 334, 108 + i // 3 * 322
        canvas.paste(face, (x, y))
        draw.text((x, y + 285), label, font=fonts[20], fill="#b8ddd1")
        sources.append({"capture": f"captures/{capture_id}.png", "label": label,
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
    draw.text((24, 754), "Seed-san Daily 衍生 · © VirtualCast, Inc. · VRM Public License 1.0", font=fonts[17], fill="#a9bad0")
    canvas.save(run / "lids-preview.png")
    (run / "lids-preview.json").write_text(json.dumps({
        "cropPixels": crop, "panelPixels": [316, 279], "refit": False,
        "sources": sources, "note": "Presentation only; metrics use original capture pixels."
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(run / "lids-preview.png")


if __name__ == "__main__":
    main()
