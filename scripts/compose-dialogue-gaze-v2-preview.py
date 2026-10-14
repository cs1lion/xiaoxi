"""Compose actual running frames; no frozen poses or geometric measurements."""
import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

parser = argparse.ArgumentParser()
parser.add_argument('evidence', type=Path)
args = parser.parse_args()
data = json.loads((args.evidence / 'results.json').read_text(encoding='utf-8'))
font_path = 'C:/Windows/Fonts/msyh.ttc'
title_font = ImageFont.truetype(font_path, 29)
label_font = ImageFont.truetype(font_path, 22)
small_font = ImageFont.truetype(font_path, 15)
styles = [('restrained', '克制'), ('natural', '自然'), ('lively', '活泼')]
moments = [('listening', '倾听 · 轻抬眉', 'listening', 'attending'),
           ('thinking', '思考 · 短暂移视', 'thinking', 'averted'),
           ('response', '实际发声 · 柔和眼神', 'speaking', 'speaking')]
width, gap, top, cell_height = 360, 18, 133, 265
sheet = Image.new('RGB', (width * 3 + gap * 4, top + cell_height * 3 + 65), '#101c29')
draw = ImageDraw.Draw(sheet)
draw.text((gap, 15), '交流眼神 V2 · 三档连续播放对照', font=title_font, fill='#eff5fd')
draw.text((gap, 59), '同一移视计划 / 普通 RAF 与音频时钟 / 眼区裁切，无冻结画面', font=small_font, fill='#b4c9db')
selected = []
for column, (style, style_label) in enumerate(styles):
    x = gap + column * (width + gap)
    draw.text((x, 96), style_label, font=label_font, fill='#92ddcf')
    for row, (moment, label, state, phase) in enumerate(moments):
        frame = next(frame for frame in data['frames']
                     if frame['style'] == style and frame['moment']['label'] == moment)
        for boundary in ['before', 'after']:
            recorded = frame[boundary]
            assert recorded['dialogue']['state'] in (['thinking', 'preparing'] if state == 'thinking' else [state])
            assert recorded['dialogue']['phase'] == phase
            assert recorded['comparison']['style'] == style
        y = top + row * cell_height
        draw.text((x, y), label, font=small_font, fill='#eff5fd')
        source = Image.open(args.evidence / frame['filename']).convert('RGB')
        # A consistent proportional crop for this eye-focus viewport. This is a
        # presentation crop, not the registered pixel calibration ROI.
        crop_box = tuple(round(value) for value in
                         (source.width*.20, source.height*.22,
                          source.width*.80, source.height*.65))
        picture = ImageOps.contain(source.crop(crop_box), (width, 222), Image.Resampling.LANCZOS)
        sheet.paste(picture, (x + (width-picture.width)//2, y + 28))
        selected.append({'label': label, 'style': style, 'frame': frame['filename'],
                         'crop': crop_box, 'before': frame['before'], 'after': frame['after']})
draw.text((gap, sheet.height - 51), '待机眨眼与身体照常运行；本图用于观感复查，不用于固定像素比较。', font=small_font, fill='#b4c9db')
draw.text((gap, sheet.height - 28), 'Seed-san © VirtualCast, Inc. 衍生技术候选', font=small_font, fill='#b4c9db')
sheet.save(args.evidence / 'contact-sheet.png')
composer = Path(__file__).read_bytes()
copy = args.evidence / 'sources/scripts/compose-dialogue-gaze-v2-preview.py'
copy.parent.mkdir(parents=True, exist_ok=True)
copy.write_bytes(composer)
(args.evidence / 'contact-sheet.json').write_text(json.dumps({
    'source': 'results.json', 'frames': selected,
    'composedAt': datetime.now(timezone.utc).isoformat(),
    'composerSha256': hashlib.sha256(composer).hexdigest()}, ensure_ascii=False, indent=2), encoding='utf-8')
print(args.evidence / 'contact-sheet.png')
