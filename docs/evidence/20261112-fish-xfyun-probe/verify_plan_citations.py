# -*- coding: utf-8 -*-
"""校验 docs/FISH_XFYUN_CLOSED_LOOP_20261112.md 里每一条 file:line 引用。"""
from __future__ import annotations

import sys

sys.stdout.reconfigure(encoding="utf-8")

CHECKS = [
    ("scripts/live_acceptance.py", 363, 'silent'),
    ("scripts/live_acceptance.py", 367, 'stepaudio'),
    ("scripts/live_acceptance.py", 380, 'edge_tts'),
    ("scripts/live_acceptance.py", 396, 'TTS_ENGINE'),
    ("scripts/live_acceptance.py", 402, 'stepaudio'),
    ("scripts/live_acceptance.py", 415, 'whisper'),
    ("scripts/live_acceptance.py", 435, 'ASR_ENGINE'),
    ("scripts/live_acceptance.py", 143, 'def probe_upstream'),
    ("scripts/live_acceptance.py", 822, 'def _start_server'),
    ("scripts/live_acceptance.py", 825, 'os.environ'),
    ("config/pacing.yaml", 55, 'target_chars: 26'),
    ("config/pacing.yaml", 56, 'max_chars: 60'),
    ("server/speech/asr.py", 136, 'model.transcribe'),
    ("docs/LIVE_ACCEPTANCE_GUIDE.md", 130, 'partial'),
    ("server/llm.py", 110, 'token'),
    ("server/llm.py", 137, 'reasoning_effort'),
    ("server/llm.py", 145, 'chat/completions'),
    ("server/llm.py", 161, 'finish_reason'),
    ("server/speech/tts.py", 403, '_cache_key'),
    ("server/speech/tts.py", 410, 'word_timings'),
    ("server/app.py", 418, 'tts_engine'),
    ("server/config.py", 240, 'tts_engine'),
    ("docs/PARKED_TASKS.md", 18, 'WordBoundary'),
    ("docs/evidence/20261109-live-acceptance/README.md", 5, 'live'),
    (".gitignore", 15, '.env'),
]

bad = 0
for path, ln, needle in CHECKS:
    try:
        line = open(path, encoding="utf-8-sig").read().splitlines()[ln - 1]
    except Exception as exc:
        print("READ-FAIL", path, ln, exc)
        bad += 1
        continue
    if needle not in line:
        print("MISMATCH", path, ln, "| want:", needle, "| got:", line.strip()[:100])
        bad += 1
print("checked", len(CHECKS), "citations, mismatches:", bad)
