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
                 upstream=None, mic=None, whisper=None, edge=None,
                 fish_tts=None, fish_asr=None, stepaudio_speech=None,
                 stepaudio_asr=None):
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
        # 2026-11-12：Fish 与 stepaudio 端点探测的默认桩都判「通」，
        # 让既有「全就绪」用例的语义不变；假绿由专门的新用例钉。
        self.fish_tts = fish_tts if fish_tts is not None else {
            "ok": True, "status": 200, "bytes": 4096, "latency_ms": 30,
            "reason": "", "model": "s2.1-pro-free", "detail": "probe ok"}
        self.fish_asr = fish_asr if fish_asr is not None else {
            "ok": True, "status": 200, "latency_ms": 30, "reason": "",
            "model": "transcribe-1-pro", "detail": "probe ok"}
        self.stepaudio_speech = stepaudio_speech if stepaudio_speech is not None else {
            "ok": True, "status": 200, "reason": "", "latency_ms": 25,
            "detail": "/audio/speech 存在"}
        self.stepaudio_asr = stepaudio_asr if stepaudio_asr is not None else {
            "ok": True, "status": 200, "reason": "", "latency_ms": 25,
            "detail": "/audio/asr/sse 存在"}

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

        def fish_tts_probe(cfg, env):
            return self.fish_tts

        def fish_asr_probe(cfg, env):
            return self.fish_asr

        def stepaudio_speech_probe(cfg, env):
            return self.stepaudio_speech

        def stepaudio_asr_probe(cfg, env):
            return self.stepaudio_asr

        return {
            "find_spec": find_spec,
            "which": which,
            "port_open": port_open,
            "upstream_probe": upstream_probe,
            "mic_probe": mic_probe,
            "whisper_probe": whisper_probe,
            "edge_probe": edge_probe,
            "fish_tts_probe": fish_tts_probe,
            "fish_asr_probe": fish_asr_probe,
            "stepaudio_speech_probe": stepaudio_speech_probe,
            "stepaudio_asr_probe": stepaudio_asr_probe,
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


class FalseGreenTests(unittest.TestCase):
    """2026-11-12：doctor 曾把「LLM 通但 TTS/ASR 端点不存在」判成就绪。

    触发场景实测：把 LLM 指向讯飞（`https://maas-api.cn-huabei-1.xf-yun.com/v1`）后，
    doctor 报「stepaudio TTS 配置就绪 / stepaudio ASR 配置就绪」，
    而讯飞根本没有 `/audio/speech` 与 `/audio/asr/sse`（都返 404 no Route matched）。
    根因：stepaudio 分支只看 LLM 的 chat/completions 通不通，而 TTS/ASR 是**不同端点**。
    """

    def test_stepaudio_tts_route_missing_is_blocked_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            deps = _Deps(stepaudio_speech={
                "ok": False, "status": 404, "reason": "route_missing",
                "latency_ms": 30,
                "detail": "/audio/speech 不存在：no Route matched with those values",
            }).as_dict()
            report = _build(tmp, deps=deps)
            self.assertFalse(report["readiness"]["ready"],
                             "LLM 通但 TTS 端点 404 时不得判就绪")
            tts = next(c for c in report["checks"] if c["id"] == "tts.engine")
            self.assertEqual(tts["status"], "blocked")
            self.assertIn("/audio/speech", tts["detail"])

    def test_stepaudio_asr_route_missing_is_blocked_not_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            deps = _Deps(stepaudio_asr={
                "ok": False, "status": 404, "reason": "route_missing",
                "latency_ms": 30,
                "detail": "/audio/asr/sse 不存在：no Route matched with those values",
            }).as_dict()
            report = _build(tmp, deps=deps)
            self.assertFalse(report["readiness"]["ready"],
                             "LLM 通但 ASR 端点 404 时不得判就绪")
            asr = next(c for c in report["checks"] if c["id"] == "asr.engine")
            self.assertEqual(asr["status"], "blocked")
            self.assertIn("/audio/asr/sse", asr["detail"])

    def test_stepaudio_route_present_but_params_wrong_is_still_ready(self):
        """400/422 = 端点在、只是探针参数没喂全；那仍然是就绪。"""
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            deps = _Deps(
                stepaudio_speech={"ok": True, "status": 400, "reason": "",
                                  "latency_ms": 25, "detail": "端点存在（参数不全）"},
                stepaudio_asr={"ok": True, "status": 400, "reason": "",
                               "latency_ms": 25, "detail": "端点存在（参数不全）"},
            ).as_dict()
            report = _build(tmp, deps=deps)
            self.assertTrue(report["readiness"]["ready"], report["readiness"])

    def test_fish_engine_requires_its_own_key_not_llm_key(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = {"tts_engine": "fish", "asr_engine": "fish"}
            report = _build(tmp, cfg=cfg, env={"LLM_API_KEY": "sk-test-SECRET"},
                            deps=_Deps().as_dict())
            statuses = {c["id"]: c["status"] for c in report["checks"]}
            self.assertEqual(statuses["tts.engine"], "skip")
            self.assertEqual(statuses["asr.engine"], "skip")

    def test_fish_engine_is_ready_when_its_probe_succeeds(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = {"tts_engine": "fish", "asr_engine": "fish"}
            env = {"LLM_API_KEY": "sk-test-SECRET", "FISH_API_KEY": "sk-fish-test"}
            report = _build(tmp, cfg=cfg, env=env, deps=_Deps().as_dict())
            self.assertTrue(report["readiness"]["ready"], report["readiness"])
            statuses = {c["id"]: c["status"] for c in report["checks"]}
            self.assertEqual(statuses["tts.engine"], "ok")
            self.assertEqual(statuses["asr.engine"], "ok")

    def test_fish_probe_failure_blocks_readiness(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            cfg = {"tts_engine": "fish", "asr_engine": "fish"}
            env = {"LLM_API_KEY": "sk-test-SECRET", "FISH_API_KEY": "sk-fish-test"}
            deps = _Deps(fish_tts={"ok": False, "status": 402, "reason": "upstream_rejected",
                                   "bytes": 0, "latency_ms": 30,
                                   "detail": "quota_exceeded"}).as_dict()
            report = _build(tmp, cfg=cfg, env=env, deps=deps)
            self.assertFalse(report["readiness"]["ready"])
            tts = next(c for c in report["checks"] if c["id"] == "tts.engine")
            self.assertEqual(tts["status"], "blocked")

    def test_fish_key_never_reaches_the_report(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            secret = "sk-fish-SECRET"
            cfg = {"tts_engine": "fish", "asr_engine": "fish"}
            env = {"LLM_API_KEY": "sk-test-SECRET", "FISH_API_KEY": secret}
            report = _build(tmp, cfg=cfg, env=env, deps=_Deps().as_dict())
            blob = json.dumps(report, ensure_ascii=False) + la.render_doctor_md(report)
            self.assertNotIn(secret, blob)
            # 但「有没有配」要留痕，否则排障时看不出是缺 key 还是缺额度。
            self.assertTrue(report["config"]["fish_api_key_present"])

    def test_probe_stepaudio_endpoint_treats_404_as_missing(self):
        """真实 HTTP 层：404 判路由不存在；400 判端点存在（参数不全）。"""
        import urllib.error
        import urllib.request

        class _Resp:
            status = 200

            def read(self):
                return b"{}"

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        calls = []

        def fake_urlopen(request, timeout=None):
            calls.append(request.full_url)
            raise urllib.error.HTTPError(
                request.full_url, 404, "Not Found", {},
                __import__("io").BytesIO(b"no Route matched with those values"))

        original = urllib.request.urlopen
        urllib.request.urlopen = fake_urlopen
        try:
            cfg = {"llm_base_url": "https://example.invalid/v1", "llm_api_key": "k",
                   "tts_model": "m", "tts_voice": "v", "asr_model_id": "a"}
            result = la.probe_stepaudio_speech(cfg, {})
        finally:
            urllib.request.urlopen = original
        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], 404)
        self.assertEqual(result["reason"], "route_missing")
        self.assertTrue(calls[0].endswith("/audio/speech"))

    def test_wav_header_helper_produces_riff_wave(self):
        raw = la._wav_bytes(b"\x00\x01" * 100, 16000)
        self.assertTrue(raw.startswith(b"RIFF"))
        self.assertIn(b"WAVE", raw[:16])
        self.assertIn(b"data", raw[:48])

    def test_as_int_keeps_zero(self):
        """0 是合法计数 —— `0 or -1` 会把「删干净了」判成「没删掉」。

        2026-11-12 实测：B4 的硬删除**其实成功**（removed_rows=1、memories 表空），
        但 `deleted_ok` 报 False，因为写的是 `int(final.get("count") or -1)`：
        count=0 是 falsy，`0 or -1` → -1，-1 != baseline(0) → 判成没删掉。
        """
        self.assertEqual(la._as_int(0), 0, "0 必须是 0，不能变成 -1")
        self.assertEqual(la._as_int(1), 1)
        self.assertEqual(la._as_int(None), -1)
        self.assertEqual(la._as_int("3"), 3)
        self.assertEqual(la._as_int("x"), -1)
        # 反例对照：旧写法会把 0 变成 -1。
        self.assertNotEqual(int(0 or -1), 0)

    def test_b4_asserts_hard_delete_returns_count_to_baseline(self):
        """B4 叫「记忆写入 → 检索命中 → 硬删除」，就必须真的断言硬删除。

        原先断言列表里没有「硬删除后计数回基线」这一条 —— 删不掉也算 pass。
        """
        import inspect

        source = inspect.getsource(la.run_live)
        self.assertIn("硬删除后计数回基线", source,
                      "B4 没有断言硬删除，checkpoint 名字与实际校验不符")


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


class _DeadProc:
    """已退出子进程的替身：poll() 非零，terminate/wait/kill 都是空操作。"""

    def __init__(self, code: int = 1) -> None:
        self._code = code

    def poll(self):
        return self._code

    def terminate(self):
        pass

    def wait(self, timeout=None):
        return self._code

    def kill(self):
        pass


def _find_assertion(checkpoint, needle: str):
    """取断言但给可读取证，不抛 StopIteration。

    旧写法 `next(a for a in ... if ...)` 在「服务没起来」时没有任何一条含目标的
    断言，于是抛 StopIteration，读起来像测试自身崩了；这里改成返回 None，由用例
    决定怎么报。
    """
    return next((a for a in checkpoint.get("assertions") or [] if needle in a["name"]), None)


def _live_args():
    import argparse

    return argparse.Namespace(wav="", mic=False, mic_seconds=4.0, expect_text="",
                              min_asr_ratio=0.6, play=False, keep_sandbox=False)


class LiveBoundaryTests(unittest.TestCase):
    def test_live_refuses_to_run_when_service_is_mock(self):
        """无 Key 时沙盘服务会进 Mock；live 必须在 L0 拒绝继续，不能产出“通过”。"""
        # Windows 上子进程退出后句柄释放可能滞后一拍；清理失败不影响断言。
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            cfg = {**BASE_CFG, "llm_api_key_present": False,
                   "tts_engine": "silent", "asr_engine": "whisper"}
            report = la.asyncio.run(la.run_live(
                root=ROOT,
                env={"LLM_API_KEY": "", "TTS_ENGINE": "silent", "ASR_ENGINE": "whisper"},
                cfg=cfg, out_dir=tmp, args=_live_args(),
                deps=_Deps().as_dict(),
            ))
            self.assertEqual(report["overall"], "fail")
            self.assertEqual(len(report["checkpoints"]), 1)
            l0 = report["checkpoints"][0]
            self.assertEqual(l0["id"], "L0.server")
            self.assertEqual(l0["status"], "fail")
            mock_assert = _find_assertion(l0, "Mock")
            if mock_assert is None:
                # 服务没起来时本用例验不了 Mock 语义。这属于环境缺失（典型是缺
                # uvicorn），不是产品断言失败；给出可读原因而不是 StopIteration。
                self.fail(f"沙盘服务未起来，无法验证 Mock 判定；请确认当前解释器可 "
                          f"`python -m uvicorn`（CI 走 requirements-ci.txt）。"
                          f"实际断言：{[a['name'] for a in l0['assertions']]}")
            self.assertFalse(mock_assert["ok"])
            self.assertNotIn(report["server"]["port"], (8000, 8010))
            self.assertTrue((tmp / "server.stderr.log").is_file())

    def test_service_that_never_starts_fails_readably(self):
        """uvicorn 缺失等导致沙盘起不来：给可读失败，不崩在断言取元素上。

        复现方式与 CI run 42525956843 同源：进程起来即死，健康检查永远不是 200。
        这里替换 start_server 让它在离线环境也确定性复现，不依赖本机装没装 uvicorn。
        """
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            cfg = {**BASE_CFG, "llm_api_key_present": False,
                   "tts_engine": "silent", "asr_engine": "whisper"}

            def dead_server(root, env, *, port, sandbox, log_dir):
                (Path(log_dir) / "server.stderr.log").write_text(
                    "ModuleNotFoundError: No module named 'uvicorn'\n", encoding="utf-8")
                return _DeadProc(1), None, None

            report = la.asyncio.run(la.run_live(
                root=ROOT,
                env={"LLM_API_KEY": ""},
                cfg=cfg, out_dir=tmp, args=_live_args(),
                deps={**_Deps().as_dict(), "start_server": dead_server,
                      "health_timeout": 0.5},
            ))
            self.assertEqual(report["overall"], "fail", "起不来绝不能算通过")
            self.assertEqual(len(report["checkpoints"]), 1)
            l0 = report["checkpoints"][0]
            self.assertEqual(l0["status"], "fail")
            names = [a["name"] for a in l0["assertions"]]
            self.assertTrue(any("服务未起来" in name for name in names), names)
            self.assertIsNone(_find_assertion(l0, "Mock"),
                              "起不来不能被伪装成 Mock 判定")
            deps_detail = l0["assertions"][0]["detail"]
            self.assertIn("No module named 'uvicorn'", deps_detail)
            self.assertIn("退出码=1", deps_detail)

    def test_start_that_raises_oserror_is_a_readable_failure(self):
        """解释器路径不存在（FileNotFoundError）也不能冒成 traceback。"""
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            cfg = {**BASE_CFG, "llm_api_key_present": False,
                   "tts_engine": "silent", "asr_engine": "whisper"}

            def broken_start(root, env, *, port, sandbox, log_dir):
                raise FileNotFoundError(2, "No such file or directory", "C:/no/such/python.exe")

            report = la.asyncio.run(la.run_live(
                root=ROOT, env={"LLM_API_KEY": ""}, cfg=cfg, out_dir=tmp,
                args=_live_args(),
                deps={**_Deps().as_dict(), "start_server": broken_start},
            ))
            self.assertEqual(report["overall"], "fail")
            l0 = report["checkpoints"][0]
            self.assertTrue(any("服务未起来" in a["name"] for a in l0["assertions"]))
            self.assertIn("启动异常", l0["assertions"][0]["detail"])
            self.assertIn("FileNotFoundError", l0["assertions"][0]["detail"])

    def test_b4_evidence_is_json_serializable(self):
        """B4 的 evidence 必须是能 JSON 序列化的 mapping。

        2026-11-12 实测踩到：原先写 `{**memory_log.calls[-8:], ...}`，
        而 `_HttpLog.calls` 是 **list**，`**list` 抛
        `TypeError: 'list' object is not a mapping` —— B4 一跑到就崩，
        整个 live 段在最后一步炸掉，B1/B2/B3 已经拿到的读数全丢。
        那条路径自 2026-10-13 以来从没真跑过（订阅阻塞），所以一直没暴露。

        这里不真起服务、不发 HTTP：用最小桩替掉 HTTP 客户端与 WebSocket，
        把 run_live 推到 B4，断言它**不抛**且 evidence 能序列化。
        """
        import json as jsonlib

        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
            tmp = Path(tmp)
            cfg = {**BASE_CFG, "llm_api_key_present": True,
                   "tts_engine": "silent", "asr_engine": "whisper"}

            class _Resp:
                def __init__(self, payload):
                    self._payload = payload
                    self.status_code = 200
                    self.text = jsonlib.dumps(payload, ensure_ascii=False)

                def json(self):
                    return self._payload

                def raise_for_status(self):
                    return None

            class _Client:
                def __init__(self, *a, **k):
                    pass

                def __enter__(self):
                    return self

                def __exit__(self, *a):
                    return False

                def request(self, method, path, **kwargs):
                    if path.endswith("/api/memories") and method == "GET":
                        return _Resp({"count": 1})
                    if path.endswith("/api/memories/context"):
                        return _Resp({"found": True, "context": "修自行车 周六"})
                    if path.endswith("/api/memories") and method == "POST":
                        return _Resp({"id": 1})
                    return _Resp({"count": 1})

            import httpx

            original_client = httpx.Client
            httpx.Client = _Client  # type: ignore[assignment]
            try:
                report = la.asyncio.run(la.run_live(
                    root=ROOT, env={"LLM_API_KEY": "sk-test"}, cfg=cfg, out_dir=tmp,
                    args=_live_args(),
                    deps={**_Deps().as_dict(),
                          "wait_health": lambda base_url, timeout=60.0: {
                              "ok": True, "llm": {"offline": False},
                              "tts": {"engine": "silent"}, "asr": {"engine": "whisper"}},
                          },
                ))
            finally:
                httpx.Client = original_client  # type: ignore[assignment]

            # 关键断言：整份报告能 JSON 序列化（这是原先崩掉的地方）。
            blob = jsonlib.dumps(report, ensure_ascii=False)
            self.assertIn("B4.memory", blob)
            b4 = next(cp for cp in report["checkpoints"] if cp["id"] == "B4.memory")
            self.assertIsInstance(b4["evidence"], dict)
            self.assertIn("calls", b4["evidence"])
            self.assertIsInstance(b4["evidence"]["calls"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
