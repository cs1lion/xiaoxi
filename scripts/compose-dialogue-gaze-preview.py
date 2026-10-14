"""Compose six real playback captures. Labels come from recorded frame states."""
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
title_font = ImageFont.truetype(font_path, 30)
label_font = ImageFont.truetype(font_path, 22)
small_font = ImageFont.truetype(font_path, 15)
selections = [
    ('normal', 0, '倾听', 'listening', 'attending'),
    ('normal', 2, '思考 · 短暂移视', 'thinking', 'averted'),
    ('normal', 4, '等待声音 · 已回看', 'preparing', 'returned'),
    ('delay', 4, '开始回应', 'speaking', 'speaking'),
    ('interrupt', 3, '打断前 · 仍在回应', 'speaking', 'speaking'),
    ('interrupt', 5, '打断后 · 恢复倾听', 'listening', 'attending'),
]
width, gap, top, cell_height = 420, 18, 98, 430
sheet = Image.new('RGB', (width * 3 + gap * 4, top + cell_height * 2 + 64), '#101c29')
draw = ImageDraw.Draw(sheet)
draw.text((gap, 16), '交流眼神 · 连续播放的实际画面', font=title_font, fill='#eff5fd')
draw.text((gap, 60), '日常下睑候选 / 原生 RAF + AudioContext / 静音口型演示', font=small_font, fill='#b4c9db')
selected = []
for position, (kind, index, label, expected_state, expected_phase) in enumerate(selections):
    frame = next(f for f in data['frames'] if f['kind'] == kind and f['index'] == index)
    for boundary in ['before', 'after']:
        state = frame[boundary]['dialogue']
        assert state['state'] == expected_state, (frame['filename'], boundary, state)
        assert state['phase'] == expected_phase, (frame['filename'], boundary, state)
    x, y = gap + (position % 3) * (width + gap), top + (position // 3) * cell_height
    draw.text((x, y), label, font=label_font, fill='#eff5fd')
    draw.text((x, y + 32), f"{kind} · {frame['measuredSeconds']:.2f} s", font=small_font, fill='#90b5cb')
    source = Image.open(args.evidence / frame['filename']).convert('RGB')
    picture = ImageOps.contain(source, (width, 365), Image.Resampling.LANCZOS)
    sheet.paste(picture, (x + (width-picture.width)//2, y + 58))
    selected.append({'label': label, 'frame': frame['filename'], 'expectedState': expected_state,
                     'expectedPhase': expected_phase, 'before': frame['before'], 'after': frame['after']})
draw.text((gap, sheet.height - 43), '来源：Seed-san © VirtualCast, Inc. 衍生技术候选。画面用于人工观感检查，不代表自然度或性能测量。',
          font=small_font, fill='#b4c9db')
sheet.save(args.evidence / 'contact-sheet.png')
composer = Path(__file__).read_bytes()
composer_copy = args.evidence / 'sources/scripts/compose-dialogue-gaze-preview.py'
composer_copy.parent.mkdir(parents=True, exist_ok=True)
composer_copy.write_bytes(composer)
(args.evidence / 'contact-sheet.json').write_text(json.dumps({'source': 'results.json', 'frames': selected,
                                                          'composedAt': datetime.now(timezone.utc).isoformat(),
                                                          'composerSha256': hashlib.sha256(composer).hexdigest()},
                                                          ensure_ascii=False, indent=2), encoding='utf-8')
print(args.evidence / 'contact-sheet.png')
