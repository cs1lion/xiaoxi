"""Compose observed running frames; no frozen poses or registered pixel claims."""
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
assert data['captureMode'] == 'timed-screenshots-without-video'
font_path = 'C:/Windows/Fonts/msyh.ttc'
title_font = ImageFont.truetype(font_path, 29)
label_font = ImageFont.truetype(font_path, 22)
small_font = ImageFont.truetype(font_path, 15)
intents = [('serious', '认真 · 更少眉变化'), ('hesitant', '犹豫 · 稍晚单侧抬眉'), ('teasing', '调侃 · 回看后带笑眼')]
moments = [('listening', '倾听', 'listening', 'attending'),
           ('thinking', '实际移视后 · 眉变化', 'thinking', 'averted'),
           ('response', '发声阶段 · 眉眼回应', 'speaking', 'speaking')]
width, gap, top, cell_height = 360, 18, 142, 265
sheet = Image.new('RGB', (width * 3 + gap * 4, top + cell_height * 3 + 82), '#101c29')
draw = ImageDraw.Draw(sheet)
draw.text((gap, 15), '交流眼神 V3 · 三种眉眼节奏', font=title_font, fill='#eff5fd')
draw.text((gap, 58), '自然风格 + base 情绪 / 同一计划种子 / 公开控件连续播放', font=small_font, fill='#b4c9db')
draw.text((gap, 82), '普通 RAF 与音频时钟；静音口型演示，未冻结画面', font=small_font, fill='#b4c9db')
selected = []
for column, (intent, intent_label) in enumerate(intents):
    x = gap + column * (width + gap)
    draw.text((x, 111), intent_label, font=label_font, fill='#92ddcf')
    for row, (moment, label, state, phase) in enumerate(moments):
        frame = next(frame for frame in data['frames'] if frame['intent'] == intent and frame['moment']['label'] == moment)
        for boundary in ['before', 'after']:
            recorded = frame[boundary]
            assert recorded['dialogue']['state'] in (['thinking', 'preparing'] if state == 'thinking' else [state])
            assert recorded['dialogue']['phase'] == phase
            assert recorded['dialogue']['intent'] == intent
            assert recorded['dialogue']['style'] == 'natural'
            assert recorded['emotion'] == 'base'
            assert recorded['reviewEnabled']
        y = top + row * cell_height
        draw.text((x, y), label, font=small_font, fill='#eff5fd')
        source = Image.open(args.evidence / frame['filename']).convert('RGB')
        crop_box = tuple(round(value) for value in (source.width*.20, source.height*.22, source.width*.80, source.height*.65))
        picture = ImageOps.contain(source.crop(crop_box), (width, 222), Image.Resampling.LANCZOS)
        sheet.paste(picture, (x + (width-picture.width)//2, y + 28))
        selected.append({'label': label, 'intent': intent, 'frame': frame['filename'],
                         'crop': crop_box, 'before': frame['before'], 'after': frame['after']})
draw.text((gap, sheet.height - 66), '眼区裁切仅供观感复查；眨眼与身体照常运行，不作固定像素比较。', font=small_font, fill='#b4c9db')
draw.text((gap, sheet.height - 43), '“犹豫”为手动呈现选择；真实情绪配置与手势仍优先。', font=small_font, fill='#b4c9db')
draw.text((gap, sheet.height - 20), 'Seed-san © VirtualCast, Inc. 衍生技术候选', font=small_font, fill='#b4c9db')
sheet.save(args.evidence / 'contact-sheet.png')
composer = Path(__file__).read_bytes()
copy = args.evidence / 'sources/scripts/compose-dialogue-gaze-v3-preview.py'
copy.parent.mkdir(parents=True, exist_ok=True)
copy.write_bytes(composer)
(args.evidence / 'contact-sheet.json').write_text(json.dumps({
    'source': 'results.json', 'frames': selected, 'composedAt': datetime.now(timezone.utc).isoformat(),
    'composerSha256': hashlib.sha256(composer).hexdigest()}, ensure_ascii=False, indent=2), encoding='utf-8')
print(args.evidence / 'contact-sheet.png')
