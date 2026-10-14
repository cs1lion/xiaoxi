"""Build a compact visual comparison from the eye browser regression captures."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

root = Path(__file__).resolve().parents[1]
out = root / "eval" / "avatar" / "eyes"
presets = [
    ("daily", "日常"), ("happy", "开心"), ("curious", "疑惑"), ("shy", "害羞"),
    ("tsundere", "傲娇"), ("sleepy", "困困"), ("focused", "认真"), ("surprised", "惊讶"),
]
font_path = "C:/Windows/Fonts/msyh.ttc"
title_font = ImageFont.truetype(font_path, 30)
label_font = ImageFont.truetype(font_path, 22)
note_font = ImageFont.truetype(font_path, 17)
sheet = Image.new("RGB", (1360, 760), "#10141d")
draw = ImageDraw.Draw(sheet)
draw.text((28, 18), "眼睛与表情联动 · 实际模型截图", font=title_font, fill="#e8edf6")
draw.text((28, 62), "8 种眉眼预设；保留注视、眨眼与说话口型", font=note_font, fill="#9caec7")
for i, (name, label) in enumerate(presets):
    capture = Image.open(out / f"daily-{name}.png").convert("RGB")
    # Keep brows, eyelids, iris and mouth together to show expression coordination.
    face = capture.crop((190, 240, 660, 645)).resize((310, 268), Image.Resampling.LANCZOS)
    x = 28 + (i % 4) * 332
    y = 110 + (i // 4) * 315
    sheet.paste(face, (x, y))
    draw.text((x + 10, y + 273), label, font=label_font, fill="#b9ddd4")
draw.text((28, 737), "Seed-san 日常衍生模型 · © VirtualCast, Inc. · VRM Public License 1.0", font=note_font, fill="#9caec7")
sheet.save(out / "contact-sheet.png")
print(out / "contact-sheet.png")
