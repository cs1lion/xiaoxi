# -*- coding: utf-8 -*-
"""真环境验收向导自身的门禁（离线，零 Key、零网络、零真实库）。

守的是三件容易退化成“恒绿”的事：

1. 无 Key 时必须明确判“未就绪”，不能把 Mock 当通过；
2. Key 存在但上游 400/401 时必须报“被上游拒绝”，不能报“有 Key 就 OK”；
3. 报告落盘时不得带出真实凭据。

用法：
    .\\.venv\\Scripts\\python.exe scripts\\test_live_acceptance.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import live_acceptance as la  # noqa: E402


class _Deps:
    """可注入的探测依赖：测试里不碰网络、不碰麦克风、不碰真实缓存。"""

    def __init__(self, *, installed=None, which=None, ports=None,
                 upstream=None, mic=None, whisper=None, edge=None):
        self.installed = installed if installed is not None else {
            "httpx", "websockets", "numpy", "faster_whisper", "edge_tts", "sounddevice",
        }
        self.which_map = which if which is not None else {"ffmpeg": "C:/bin/ffmpeg.exe",
                                                          "ffplay": "C:/bin/ffplay.exe"}
        self.ports = ports if ports is not None else {}
        self.upstream = upstream
        self.mic = mic if mic is not None else {"available": True, "count": 1, "error": None}
        self.whisper = whisper if whisper is not None else {"available": True, "detail": "small"}
        self.edge = edge if edge is not None else {"reachable": True}

    def as_dict(self):
        def find_spec(name):
            return object() if name in self.installed else None

        def which(name):
            return self.which_map.get(name)

        def port_open(host, port):
            return self.ports.get(port, False)

        def upstream_probe(cfg, env):
            if self.upstream is not None:
                return self.upstream
            return {"ok": True, "status": 200, "latency_ms": 20, "excerpt": "", "reason": ""}

        def mic_probe():
            return self.mic

        def whisper_probe(cfg, env, root):
            return self.whisper

        def edge_probe():
            return self.edge

        return {
            "find_spec": find_spec,
            "which": which,
            "port_open": port_open,
            "upstream_probe": upstream_probe,
            "mic_probe": mic_probe,
            "whisper_probe": whisper_probe,
            "edge_probe": edge_probe,
            "now": lambda: datetime(2026, 11, 9, 10, 0, 0),
        }


BASE_CFG = {
    "llm_provider": "openai",
    "llm_base_url": "https://api.stepfun.com/step_plan/v1",
    "llm_model": "step-3.7-flash",
    "llm_api_key_present": True,
    "tts_engine": "stepaudio",
    "asr_engine": "stepaudio",
    "asr_model": "small",
    "asr_model_id": "stepaudio-2.5-asr",
    "asr_stream_url": "",
}


def _build(tmp: Path, *, env=None, cfg=None, deps=None):
    env = dict(env or {"LLM_API_KEY": "sk-test-SECRET"})
    return la.build_doctor(
        root=tmp,
        env=env,
        cfg={**BASE_CFG, **(cfg or {})},
        out_dir=tmp / "out",
        deps=deps or _Deps().as_dict(),
    )


class DoctorTests(unittest.TestCase):
    def test_no_key_is_not_ready_and_names_what_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report = _build(tmp, env={}, cfg={"llm_api_key_present": False},
                            deps=_Deps(upstream={"ok": False, "status": 0, "reason": "no_key"}).as_dict())
            self.assertFalse(report["readiness"]["ready"])
            ids = {item["id"] for item in report["readiness"]["missing"]}
            self.assertIn("env.key", ids)
            md = la.render_doctor_md(report)
            self.assertIn("还差什么", md)
            self.assertIn("LLM_API_KEY", md)

    def test_key_present_but_upstream_400_is_blocked_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            deps = _Deps(upstream={
                "ok": False, "status": 400, "latency_ms": 120,
                "excerpt": '{"error":{"message":"subscription is not active"}}',
                "reason": "upstream_rejected",
            }).as_dict()
            report = _build(tmp, deps=deps)
            self.assertFalse(report["readiness"]["ready"])
            upstream = next(c for c in report["checks"] if c["id"] == "llm.upstream")
            self.assertEqual(upstream["status"], "blocked")
            md = la.render_doctor_md(report)
            self.assertIn("被上游拒绝", md)
            self.assertIn("Mock", md)

    def test_fully_ready_when_key_and_upstream_and_engines_are_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report = _build(tmp, deps=_Deps().as_dict())
            self.assertTrue(report["readiness"]["ready"], report["readiness"])
            missing = report["readiness"]["missing"]
            self.assertEqual(missing, [])

    def test_warn_and_info_checks_do_not_block_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            deps = _Deps(which={}, mic={"available": False, "count": 0, "error": "no mic"}).as_dict()
            report = _build(tmp, deps=deps)
            self.assertTrue(report["readiness"]["ready"], report["readiness"])
            statuses = {c["id"]: c["status"] for c in report["checks"]}
            self.assertEqual(statuses["runtime.ffmpeg"], "warn")
            self.assertEqual(statuses["asr.input"], "warn")

    def test_secret_values_never_reach_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            secret = "sk-test-SECRET"
            report = _build(tmp, env={"LLM_API_KEY": secret},
                            deps=_Deps(upstream={"ok": False, "status": 401, "latency_ms": 10,
                                                 "excerpt": f"bad key {secret}",
                                                 "reason": "upstream_rejected"}).as_dict())
            blob = json.dumps(report, ensure_ascii=False) + la.render_doctor_md(report)
            self.assertNotIn(secret, blob)

    def test_whisper_cache_missing_is_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = {"asr_engine": "whisper", "asr_model": "small"}
            deps = _Deps(whisper={"available": False, "detail": "no model"}).as_dict()
            report = _build(tmp, cfg=cfg, deps=deps)
            self.assertFalse(report["readiness"]["ready"])
            asr = next(c for c in report["checks"] if c["id"] == "asr.engine")
            self.assertEqual(asr["status"], "missing")
            self.assertIn("下载", asr["how_to_fix"])

    def test_silent_tts_is_not_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            report = _build(tmp, cfg={"tts_engine": "silent"})
            self.assertFalse(report["readiness"]["ready"])
            tts = next(c for c in report["checks"] if c["id"] == "tts.engine")
            self.assertEqual(tts["status"], "missing")


class CliTests(unittest.TestCase):
    def test_doctor_exits_zero_without_key_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code = la.main(
                ["--doctor", "--out", str(tmp / "out")],
                env={}, cfg={**BASE_CFG, "llm_api_key_present": False},
                deps=_Deps(upstream={"ok": False, "status": 0, "reason": "no_key"}).as_dict(),
            )
            self.assertEqual(code, 0)
            files = list((tmp / "out").rglob("doctor.json"))
            self.assertEqual(len(files), 1, files)
            self.assertEqual(len(list((tmp / "out").rglob("doctor.md"))), 1)
            payload = json.loads(files[0].read_text(encoding="utf-8"))
            self.assertFalse(payload["readiness"]["ready"])
            self.assertIn("LLM_API_KEY", files[0].with_name("doctor.md").read_text(encoding="utf-8"))

    def test_doctor_strict_exits_two_when_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code = la.main(
                ["--doctor", "--strict", "--out", str(tmp / "out")],
                env={}, cfg={**BASE_CFG, "llm_api_key_present": False},
                deps=_Deps(upstream={"ok": False, "status": 0, "reason": "no_key"}).as_dict(),
            )
            self.assertEqual(code, 2)

    def test_doctor_still_exits_zero_without_ffmpeg_or_microphone(self):
        """全新 clone 缺 ffmpeg/ffprobe、没有麦克风时，--doctor 只报缺，不报错退出。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            code = la.main(
                ["--doctor", "--out", str(Path(tmp) / "out")],
                env={}, cfg={**BASE_CFG, "llm_api_key_present": False},
                deps=_Deps(which={}, mic={"available": False, "count": 0, "error": "no mic"},
                           upstream={"ok": False, "status": 0, "reason": "no_key"}).as_dict(),
            )
            self.assertEqual(code, 0)
            doctor_path = next((Path(tmp) / "out").rglob("doctor.json"))
            payload = json.loads(doctor_path.read_text(encoding="utf-8"))
            statuses = {c["id"]: c["status"] for c in payload["checks"]}
            self.assertEqual(statuses["runtime.ffmpeg"], "warn")
            self.assertEqual(statuses["asr.input"], "warn")
            self.assertEqual(statuses["env.key"], "missing")
            self.assertFalse(payload["readiness"]["ready"])
            md = doctor_path.with_name("doctor.md").read_text(encoding="utf-8")
            self.assertIn("没有可用麦克风", md)
            self.assertIn("runtime.ffmpeg", md)

    def test_missing_wav_fails_fast_before_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = la.main(
                ["--wav", str(Path(tmp) / "nope.wav"), "--out", str(Path(tmp) / "out")],
                env={"LLM_API_KEY": "sk-test-SECRET"}, cfg=BASE_CFG,
                deps=_Deps().as_dict(),
            )
            self.assertEqual(code, 2)
            self.assertEqual(list((Path(tmp) / "out").rglob("run.json")), [])

    def test_mic_flag_without_device_fails_fast_before_live(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = la.main(
                ["--mic", "--out", str(Path(tmp) / "out")],
                env={"LLM_API_KEY": "sk-test-SECRET"}, cfg=BASE_CFG,
                deps=_Deps(mic={"available": False, "count": 0, "error": "no mic"}).as_dict(),
            )
            self.assertEqual(code, 2)
            self.assertEqual(list((Path(tmp) / "out").rglob("run.json")), [])

    def test_default_out_dir_is_date_stamped_under_docs_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = la.evidence_root(tmp, date=datetime(2026, 11, 9))
            self.assertEqual(path, Path(tmp) / "docs" / "evidence" / "20261109-live-acceptance")


class _FakeWS:
    def __init__(self, frames):
        self.frames = list(frames)

    async def recv(self):
        return json.dumps(self.frames.pop(0))


class HelperTests(unittest.TestCase):
    """B1–B3 的事件解析是纯逻辑，可离线钉住；真实音频出口仍需人跑。"""

    def test_collect_turn_assembles_text_and_audio_until_done(self):
        import asyncio
        import base64

        wav = b"RIFF" + b"\x00" * 4 + b"WAVE" + b"\x00" * 8
        frames = [
            {"type": "text_delta", "delta": "[开心]"},
            {"type": "emotion", "emotion": "开心"},
            {"type": "audio", "data": base64.b64encode(wav).decode()},
            {"type": "text_delta", "delta": "你好"},
            {"type": "done", "text": "[开心]你好"},
        ]
        events, text, first_ms, timed_out = asyncio.run(
            la._collect_turn(_FakeWS(frames), timeout=1.0)
        )
        self.assertFalse(timed_out)
        self.assertEqual(text, "[开心]你好")
        self.assertIsNotNone(first_ms)
        self.assertEqual(len(la._audio_bytes(events)), 1)
        self.assertEqual(la._audio_ext(la._audio_bytes(events)[0]), "wav")

    def test_collect_turn_stops_on_terminal_error_but_not_recoverable(self):
        import asyncio

        terminal = [{"type": "error", "terminal": True, "message": "boom"}]
        events, text, _, timed_out = asyncio.run(la._collect_turn(_FakeWS(terminal), timeout=1.0))
        self.assertEqual(len(events), 1)
        self.assertFalse(timed_out)

        frames = [
            {"type": "error", "recoverable": True, "message": "tts down"},
            {"type": "text", "text": "继续"},
            {"type": "done", "text": "继续"},
        ]
        events, text, _, timed_out = asyncio.run(la._collect_turn(_FakeWS(frames), timeout=1.0))
        self.assertEqual(text, "继续")
        self.assertFalse(timed_out)

    def test_similarity_ignores_punctuation_strips_emotion_tag(self):
        expected = "我昨天说的那个面试，今天下午就要开始了。"
        actual = "[平静]我昨天说的那个面试今天下午就要开始了"
        self.assertGreaterEqual(la._similarity(expected, actual), 0.9)
        self.assertEqual(la._norm_text("[开心]你好！"), "开心你好")

    def test_wav_parsing_resamples_to_16k_mono_pcm(self):
        import math
        import wave

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tone.wav"
            rate, seconds = 8000, 0.5
            with wave.open(str(path), "wb") as wav:
                wav.setnchannels(1)
                wav.setsampwidth(2)
                wav.setframerate(rate)
                wav.writeframes(b"".join(
                    int(0.3 * 32767 * math.sin(2 * math.pi * 440 * i / rate)).to_bytes(2, "little", signed=True)
                    for i in range(int(rate * seconds))
                ))
            pcm, out_rate, info = la._pcm16_from_wav(path)
            self.assertEqual(out_rate, 16000)
            self.assertGreater(len(pcm), 0)
            self.assertLessEqual(info["duration_ms"], 600)
            self.assertGreater(info["peak"], 0.1)

    def test_summary_folds_audio_payload_to_byte_count(self):
        event = la._summarize_event({"type": "audio", "data": "A" * 100})
        self.assertIn("base64 100 chars", event["data"])


class LiveBoundaryTests(unittest.TestCase):
    def test_live_refuses_to_run_when_service_is_mock(self):
        """无 Key 时沙盘服务会进 Mock；live 必须在 L0 拒绝继续，不能产出“通过”。"""
        import argparse

        # Windows 上子进程退出后句柄释放可能滞后一拍；清理失败不影响断言。
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            cfg = {**BASE_CFG, "llm_api_key_present": False,
                   "tts_engine": "silent", "asr_engine": "whisper"}
            args = argparse.Namespace(wav="", mic=False, mic_seconds=4.0, expect_text="",
                                      min_asr_ratio=0.6, play=False, keep_sandbox=False)
            report = la.asyncio.run(la.run_live(
                root=ROOT,
                env={"LLM_API_KEY": "", "TTS_ENGINE": "silent", "ASR_ENGINE": "whisper"},
                cfg=cfg, out_dir=tmp, args=args,
                deps=_Deps().as_dict(),
            ))
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(len(report["checkpoints"]), 1)
            l0 = report["checkpoints"][0]
            self.assertEqual(l0["id"], "L0.server")
            self.assertEqual(l0["status"], "fail")
            mock_assert = next(a for a in l0["assertions"] if "Mock" in a["name"])
            self.assertFalse(mock_assert["ok"])
            self.assertNotIn(report["server"]["port"], (8000, 8010))
            self.assertTrue((tmp / "server.stderr.log").is_file())


if __name__ == "__main__":
    unittest.main(verbosity=2)
