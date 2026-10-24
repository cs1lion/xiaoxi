# -*- coding: utf-8 -*-
"""dogfood 模拟的语音合成器：PowerShell SAPI（Huihui）把每个话段合成
16kHz/16bit/单声道 WAV，再按时间轴拼接成一天的「假麦克风」输入。

Run: ./.venv/Scripts/python.exe tmp/dogfood/synthesize_day.py
产出：tmp/dogfood/utt_NN.wav 与 tmp/dogfood/day.wav（+ day_plan.json 时间轴）
"""
from __future__ import annotations

import json
import subprocess
import wave
from pathlib import Path

HERE = Path(__file__).resolve().parent

# (文本, 话后静音秒)——间隔给足她把话说完；验收那句前留 20s 等沙盒工跑完。
SCRIPT = [
    ("早上好呀小溪，新的一天开始啦。", 6.0),
    ("你还记得我的猫叫什么名字吗？", 7.0),
    ("对了，记住一件事，我最近喜欢在傍晚出去散步。", 7.0),
    ("帮我安排一个沙盒打样，生成并校验一个小文件吧。", 20.0),
    ("这一单做得不错，就先收工吧。", 8.0),
    ("今天晚上我想吃点清淡的，你有什么建议吗？", 7.0),
    ("陪我随便聊聊天，讲讲你今天都在忙什么。", 9.0),
    ("好了，我去忙了，回见。", 6.0),
]

RATE = 16000
SILENCE_SECONDS_LEAD = 2.0


def synth_one(text: str, out: Path) -> None:
    ps = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.SelectVoice('Microsoft Huihui Desktop'); "
        "$s.Rate = 0; "
        "$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo("
        "16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, "
        "[System.Speech.AudioFormat.AudioChannel]::Mono); "
        f"$s.SetOutputToWaveFile('{out}', $fmt); "
        f"$s.Speak('{text}'); "
        "$s.Dispose()"
    )
    subprocess.run(["powershell", "-NoProfile", "-Command", ps],
                   check=True, capture_output=True)


def main() -> None:
    parts: list[tuple[Path, float]] = []
    for index, (text, gap) in enumerate(SCRIPT, start=1):
        wav = HERE / f"utt_{index:02d}.wav"
        if not wav.exists():
            print(f"synth {wav.name}: {text}")
            synth_one(text, wav)
        parts.append((wav, gap))

    with wave.open(str(HERE / "utt_01.wav"), "rb") as probe:
        assert probe.getframerate() == RATE and probe.getnchannels() == 1, \
            f"SAPI 输出格式意外: {probe.getframerate()}Hz/{probe.getnchannels()}ch"

    silence_lead = b"\x00" * int(RATE * 2) * 2
    frames = bytearray(silence_lead)
    timeline = []
    clock = SILENCE_SECONDS_LEAD
    for wav, gap in parts:
        with wave.open(str(wav), "rb") as reader:
            data = reader.readframes(reader.getnframes())
        frames.extend(data)
        duration = len(data) / 2 / RATE
        timeline.append({"start": round(clock, 2), "duration": round(duration, 2),
                         "text": SCRIPT[parts.index((wav, gap))][0]})
        clock += duration + gap
        frames.extend(b"\x00" * int(RATE * gap) * 2)

    out = HERE / "day.wav"
    with wave.open(str(out), "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(RATE)
        writer.writeframes(bytes(frames))
    (HERE / "day_plan.json").write_text(
        json.dumps({"total_seconds": round(clock, 1), "utterances": timeline},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"day.wav: {clock:.0f}s, {len(timeline)} utterances -> {out}")


if __name__ == "__main__":
    main()
