"""Compose a fixed-crop preview from a completed eye calibration run."""
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
        ("baseline-curious", "原生眉形 · 疑惑"),
        ("candidate-curious", "独立抬眉 · 疑惑"),
        ("brow-left-100", "画面左眉 · 满强度"),
        ("brow-right-100", "画面右眉 · 满强度"),
        ("happy-mouth-open", "开心 · 开口"),
        ("brow-stress-up-blink-100", "疑惑 · 注视 + 全闭 + 开口"),
    ]
    crop = (170, 210, 555, 550)
    fonts = {size: ImageFont.truetype(args.font, size) for size in (17, 20, 29)}
    canvas = Image.new("RGB", (1032, 796), "#101722")
    draw = ImageDraw.Draw(canvas)
    draw.text((24, 18), "眉眼升级 · 实际渲染对照", font=fonts[29], fill="#e6edf7")
    draw.text((24, 62), "同一模型、相机与裁切坐标；原生 / 新形变比较保留相同参数", font=fonts[17], fill="#a9bad0")
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
    canvas.save(run / "upgrade-preview.png")
    (run / "upgrade-preview.json").write_text(json.dumps({
        "cropPixels": crop, "panelPixels": [316, 279], "refit": False,
        "sources": sources, "note": "Presentation only; metrics use original capture pixels."
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(run / "upgrade-preview.png")


if __name__ == "__main__":
    main()
