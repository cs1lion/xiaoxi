"""Sample real video frames for human QA; extraction rate is not render FPS."""
import argparse
import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageOps

parser = argparse.ArgumentParser()
parser.add_argument('evidence', type=Path)
args = parser.parse_args()
data = json.loads((args.evidence / 'results.json').read_text(encoding='utf-8'))
assert data['captureMode'] == 'clean-video-without-screenshots'
video = args.evidence / data['video']
ffmpeg, ffprobe = shutil.which('ffmpeg'), shutil.which('ffprobe')
assert ffmpeg and ffprobe, 'ffmpeg and ffprobe must be installed'
probe = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_entries',
    'format=duration:stream=width,height', '-of', 'json', str(video)], text=True))
duration = float(probe['format']['duration'])
# Playwright exposes neither its first screencast frame timestamp nor an exact
# correspondence with performance.now(). End alignment is only a convenience
# for choosing a window; frame labels use actual encoded-video time.
offset = duration - data['trace'][-1]['now'] / 1000
font_path = 'C:/Windows/Fonts/msyh.ttc'
title_font = ImageFont.truetype(font_path, 24)
small_font = ImageFont.truetype(font_path, 14)
jobs = [('serious', 'averted', '认真 · 移视与回看'),
        ('hesitant', 'averted', '犹豫 · 移视后眉变化'),
        ('teasing', 'averted', '调侃 · 移视与回看'),
        ('teasing', 'speaking', '调侃 · 发声后的眉眼与控制优先级')]
records = []
for intent, phase, title in jobs:
    observed = next(frame for frame in data['trace'] if frame.get('captureIntent') == intent
                    and frame['intent'] == intent and frame['phase'] == phase)
    at = max(0, observed['now'] / 1000 + offset - .15)
    directory = args.evidence / 'video-frames' / f'{intent}-{phase}'
    directory.mkdir(parents=True, exist_ok=True)
    command = [ffmpeg, '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{at:.6f}',
               '-i', str(video), '-t', '1.5', '-vf', 'fps=10', '-frames:v', '15', str(directory / '%02d.png')]
    subprocess.run(command, check=True)
    images = sorted(directory.glob('*.png'))
    assert len(images) == 15, f'Expected 15 sampled video frames, got {len(images)}'
    width, height, gap, top = 250, 193, 12, 87
    sheet = Image.new('RGB', (5*width+6*gap, top+3*height+38), '#101c29')
    draw = ImageDraw.Draw(sheet)
    draw.text((gap, 12), title + ' · 独立连续视频抽帧', font=title_font, fill='#eff5fd')
    draw.text((gap, 45), '每 0.1 秒取一帧，仅供连续动作复查；抽样频率不是渲染帧率。', font=small_font, fill='#b4c9db')
    draw.text((gap, 65), '窗口按结尾近似对齐观察日志；以下时间为编码视频时间。', font=small_font, fill='#b4c9db')
    for index, path in enumerate(images):
        x, y = gap+(index % 5)*(width+gap), top+(index//5)*height
        source = Image.open(path).convert('RGB')
        box = tuple(round(value) for value in
                    (source.width*.17, source.height*.20, source.width*.83, source.height*.65))
        picture = ImageOps.contain(source.crop(box), (width, height-30), Image.Resampling.LANCZOS)
        draw.text((x, y), f'{at+index*.1:.2f} 秒', font=small_font, fill='#b4c9db')
        sheet.paste(picture, (x+(width-picture.width)//2, y+23))
    draw.text((gap, sheet.height-26), '源视频保留眨眼、身体及已有手势的优先控制，无冻结 / 无浏览器跳帧。', font=small_font, fill='#b4c9db')
    filename = f'video-strip-{intent}-{phase}.png'
    sheet.save(args.evidence / filename)
    records.append({'intent': intent, 'phaseWindow': phase, 'strip': filename, 'videoStartSeconds': at,
                    'lengthSeconds': 1.5, 'frameStepSeconds': .1, 'observation': observed,
                    'alignment': 'Approximate end alignment only; video frame time labels are the extraction times.'})
source = Path(__file__).read_bytes()
copy = args.evidence / 'sources/scripts/compose-dialogue-gaze-v3-video.py'
copy.parent.mkdir(parents=True, exist_ok=True)
copy.write_bytes(source)
(args.evidence / 'video-strips.json').write_text(json.dumps({'video': data['video'],
    'videoSha256': hashlib.sha256(video.read_bytes()).hexdigest(), 'durationSeconds': duration,
    'estimatedVideoOffsetSeconds': offset, 'composerSha256': hashlib.sha256(source).hexdigest(),
    'samplingNotice': '10 Hz extraction is not measured rendering FPS.', 'strips': records},
    ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps({'evidence': str(args.evidence), 'durationSeconds': duration,
                  'estimatedOffsetSeconds': offset, 'strips': [item['strip'] for item in records]}, ensure_ascii=False))
