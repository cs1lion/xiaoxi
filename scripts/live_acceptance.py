# -*- coding: utf-8 -*-
"""真环境验收向导：把“端到端从没在真实环境验证过”变成一条可跑的命令。

设计边界
--------
- **无 Key 也能跑**：`--doctor` 只做体检，不降级、不发起真实合成/识别；
  缺什么就列什么，并给出补法。
- **有 Key 也不等于真实可用**：Key 存在时体检会真发一次 `max_tokens=1` 的
  上游探测；400/401/402/403 一律判“被上游拒绝”，不判通过。
- **不碰用户正在用的服务**：live 阶段在独立端口（默认 8721 起、拒绝 8000/8010）
  起 `server.app`，并把 `DB_PATH` / `APP_DATA_DIR` / `TEST_SENTINEL=1` 钉在
  临时沙盘；退出时只杀自己起的子进程。
- **Mock 不等于通过**：服务 `/api/health` 报 `llm.offline=true` 时，live 阶段
  直接判“拒绝验收”，不继续跑检查点，也不把 Mock 回复写进通过项。
- **凭据不出报告**：报告只记录“有/无”，错误响应体按环境里的 KEY/TOKEN/SECRET
  值做脱敏后再截断。

用法
----
    .\\.venv\\Scripts\\python.exe scripts\\live_acceptance.py --doctor
    .\\.venv\\Scripts\\python.exe scripts\\live_acceptance.py --doctor --strict
    .\\.venv\\Scripts\\python.exe scripts\\live_acceptance.py --mic
    .\\.venv\\Scripts\\python.exe scripts\\live_acceptance.py --wav .\\rec.wav ^
        --expect-text "我昨天说的那个面试，今天下午就要开始了。"

    --doctor   只体检；缺依赖时仍退出 0（证明脚本本身是活的）
    --strict   体检不通过时退出 2（给 CI / 人工门禁用）
    --mic      真人麦克风录 4 秒（默认台词见 DEFAULT_ASR_PHRASE）
    --wav      用现成录音；配 --expect-text 可算转写相似度
    --play     用 ffplay 播放 TTS 产物（默认只落盘、不自动出声）

退出码
------
    0  体检完成且 live 检查点全部通过
    1  live 有失败或只跑到“参考”档
    2  未就绪（缺 Key / 上游拒绝 / 引擎非 live / 缺模型），未开跑 live
"""
from __future__ import annotations

import argparse
import asyncio
import base64
import difflib
import importlib.util
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping

ROOT = Path(__file__).resolve().parent.parent
LOCAL_PORTS = (8000, 8010)
DEFAULT_ASR_PHRASE = "我昨天说的那个面试，今天下午就要开始了。"
REQUIRED_MODULES = ("httpx", "websockets", "numpy")
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")


# ---------------------------------------------------------------- 基础工具


def _now(value: datetime | None = None) -> datetime:
    return value or datetime.now()


def evidence_root(root: Path = ROOT, *, date: datetime | None = None) -> Path:
    stamp = _now(date).strftime("%Y%m%d")
    return Path(root) / "docs" / "evidence" / f"{stamp}-live-acceptance"


def _run_dir(out_dir: Path, *, now: datetime | None = None) -> Path:
    return Path(out_dir) / f"run-{_now(now).strftime('%Y%m%d-%H%M%S')}"


def redact(text: Any, env: Mapping[str, str]) -> str:
    """去掉环境里的凭据值，再截断；只用于错误体与日志摘录。"""
    value = str(text or "")
    for key, secret in env.items():
        if not secret or not any(marker in key.upper() for marker in SECRET_MARKERS):
            continue
        if len(secret) >= 6:
            value = value.replace(secret, "<redacted>")
    value = re.sub(r"sk-[A-Za-z0-9_-]{8,}", "<redacted>", value)
    value = re.sub(r"Bearer\s+[A-Za-z0-9._-]{8,}", "Bearer <redacted>", value)
    return value[:400]


def port_open(host: str, port: int, *, timeout: float = 0.6) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _free_port(*, preferred: range = range(8721, 8799)) -> int:
    for port in preferred:
        if port in LOCAL_PORTS:
            continue
        if port_open("127.0.0.1", port):
            continue
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.bind(("127.0.0.1", port))
            return port
        except OSError:
            continue
    raise RuntimeError("找不到可用的独立端口（8721-8798 全被占用）")


def _check(
    check_id: str,
    *,
    group: str,
    status: str,
    summary: str,
    detail: str = "",
    how_to_fix: str = "",
    required: bool = True,
) -> dict[str, Any]:
    return {
        "id": check_id,
        "group": group,
        "status": status,
        "required": required,
        "summary": summary,
        "detail": detail,
        "how_to_fix": how_to_fix,
    }


# ---------------------------------------------------------------- 探测实现


def probe_upstream(cfg: Mapping[str, Any], env: Mapping[str, str], *, timeout: float = 15.0) -> dict[str, Any]:
    """一次极小的真实请求：只回答“上游认不认这把 Key”。

    不把 Key 写进返回值；错误体先脱敏再截断。`max_tokens=1` 是为了让代价
    接近零，同时足以区分 400/401/402/403 与“网络不可达”。
    """
    key = str(cfg.get("llm_api_key") or env.get("LLM_API_KEY") or "")
    base_url = str(cfg.get("llm_base_url") or "").rstrip("/")
    model = str(cfg.get("llm_model") or "")
    if not key:
        return {"ok": False, "status": 0, "reason": "no_key", "latency_ms": 0, "excerpt": ""}
    if not base_url:
        return {"ok": False, "status": 0, "reason": "no_base_url", "latency_ms": 0, "excerpt": ""}
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
        "temperature": 0,
        "stream": False,
    }
    request = urllib.request.Request(
        f"{base_url}/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return {
                "ok": response.status < 400,
                "status": response.status,
                "reason": "" if response.status < 400 else "upstream_rejected",
                "latency_ms": int((time.perf_counter() - started) * 1000),
                "excerpt": redact(body, env),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return {
            "ok": False,
            "status": exc.code,
            "reason": "upstream_rejected",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "excerpt": redact(body, env),
        }
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return {
            "ok": False,
            "status": 0,
            "reason": "unreachable",
            "latency_ms": int((time.perf_counter() - started) * 1000),
            "excerpt": redact(str(exc), env),
        }


def _hf_cache_dir(env: Mapping[str, str]) -> Path:
    if env.get("HF_HUB_CACHE"):
        return Path(env["HF_HUB_CACHE"])
    if env.get("HF_HOME"):
        return Path(env["HF_HOME"]) / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def probe_whisper(cfg: Mapping[str, Any], env: Mapping[str, str], root: Path) -> dict[str, Any]:
    """只读检查 faster-whisper 模型是否已在本地；绝不触发下载。"""
    model = str(cfg.get("asr_model") or "small")
    candidate = Path(model)
    if candidate.is_absolute() or "/" in model or "\\" in model:
        ok = candidate.is_dir() and any(candidate.glob("*.bin"))
        return {"available": ok, "detail": str(candidate), "cache": "local_path"}
    cache = _hf_cache_dir(env)
    repo = cache / f"models--Systran--faster-whisper-{model}"
    if repo.is_dir() and any(repo.glob("snapshots/*/model.bin")):
        return {"available": True, "detail": str(repo), "cache": str(cache)}
    others = sorted(p.name.removeprefix("models--Systran--faster-whisper-")
                    for p in cache.glob("models--Systran--faster-whisper-*"))
    return {
        "available": False,
        "detail": f"未找到 {model}；已缓存尺寸={others or ['无']}",
        "cache": str(cache),
    }


def probe_edge_network(*, timeout: float = 5.0) -> dict[str, Any]:
    try:
        with socket.create_connection(("speech.platform.bing.com", 443), timeout=timeout):
            return {"reachable": True}
    except OSError as exc:
        return {"reachable": False, "detail": str(exc)}


def probe_mic() -> dict[str, Any]:
    try:
        import sounddevice as sd

        devices = sd.query_devices()
        inputs = [d for d in devices if int(d.get("max_input_channels") or 0) > 0]
        return {"available": bool(inputs), "count": len(inputs), "error": None}
    except Exception as exc:  # PortAudio 缺失 / 驱动异常都只影响真人 ASR
        return {"available": False, "count": 0, "error": str(exc)}


def _fmt_ports() -> str:
    parts = []
    for port in LOCAL_PORTS:
        state = "占用" if port_open("127.0.0.1", port) else "空闲"
        parts.append(f"{port}={state}")
    return "，".join(parts)


# ---------------------------------------------------------------- 体检报告


def assess_readiness(checks: list[dict[str, Any]]) -> dict[str, Any]:
    missing = [
        {
            "id": item["id"],
            "summary": item["summary"],
            "how_to_fix": item["how_to_fix"],
        }
        for item in checks
        if item["required"] and item["status"] != "ok"
    ]
    return {
        "ready": not missing,
        "missing": missing,
        "warn": [item["id"] for item in checks if item["status"] == "warn"],
    }


def build_doctor(
    *,
    root: Path,
    env: Mapping[str, str],
    cfg: Mapping[str, Any],
    out_dir: Path,
    deps: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(root)
    out_dir = Path(out_dir)
    checks: list[dict[str, Any]] = []

    # ---- 解释器与依赖
    venv_py = root / ".venv" / "Scripts" / "python.exe"
    same_python = venv_py.is_file() and Path(sys.executable).resolve() == venv_py.resolve()
    checks.append(_check(
        "runtime.python", group="runtime",
        status="ok" if same_python else "warn",
        summary=("正在用项目 .venv 解释器" if same_python else "不是项目 .venv 解释器"),
        detail=f"sys.executable={sys.executable}；venv={venv_py}",
        how_to_fix="" if same_python else "用 .\\.venv\\Scripts\\python.exe 运行本脚本",
        required=False,
    ))

    missing_modules = [name for name in REQUIRED_MODULES if deps["find_spec"](name) is None]
    checks.append(_check(
        "runtime.deps", group="runtime",
        status="missing" if missing_modules else "ok",
        summary=("缺依赖：" + ", ".join(missing_modules)) if missing_modules else "httpx/websockets/numpy 可用",
        detail=f"required={list(REQUIRED_MODULES)}",
        how_to_fix="uv sync（或按 uv.lock 安装）" if missing_modules else "",
    ))

    ffmpeg = deps["which"]("ffmpeg")
    ffplay = deps["which"]("ffplay")
    checks.append(_check(
        "runtime.ffmpeg", group="runtime",
        status="ok" if ffmpeg and ffplay else "warn",
        summary=("ffmpeg/ffplay 可用" if ffmpeg and ffplay else
                 f"缺 {'ffmpeg' if not ffmpeg else ''}{'/' if not ffmpeg and not ffplay else ''}{'ffplay' if not ffplay else ''}"),
        detail=f"ffmpeg={ffmpeg}；ffplay={ffplay}",
        how_to_fix="装 ffmpeg（choco install ffmpeg），--play 与音频转换会用到" if not (ffmpeg and ffplay) else "",
        required=False,
    ))

    # ---- Key 与上游
    key_present = bool(cfg.get("llm_api_key_present"))
    checks.append(_check(
        "env.key", group="config",
        status="ok" if key_present else "missing",
        summary=("LLM_API_KEY 已配置（只看是否存在，不打印值）" if key_present
                 else "LLM_API_KEY 未配置"),
        detail=f"provider={cfg.get('llm_provider')}；base_url={cfg.get('llm_base_url')}；model={cfg.get('llm_model')}",
        how_to_fix="" if key_present else "在 .env 里填 LLM_API_KEY（见 .env.example；StepFun 还需订阅/模型权限生效）",
    ))

    upstream: dict[str, Any] = {"ok": False, "status": 0, "reason": "no_key", "latency_ms": 0, "excerpt": ""}
    if key_present:
        upstream = dict(deps["upstream_probe"](cfg, env))
        if upstream.get("excerpt"):
            upstream["excerpt"] = redact(upstream["excerpt"], env)
    upstream_ok = bool(upstream.get("ok"))
    if not key_present:
        upstream_status = "skip"
        upstream_summary = "无 Key，未探测上游（Mock 模式的边界见下方）"
    elif upstream_ok:
        upstream_status = "ok"
        upstream_summary = f"上游可用（HTTP {upstream.get('status')}，{upstream.get('latency_ms')}ms）"
    elif upstream.get("reason") == "unreachable" or upstream.get("status") == 0:
        upstream_status = "blocked"
        upstream_summary = "上游不可达（网络/DNS/代理）"
    else:
        upstream_status = "blocked"
        upstream_summary = f"被上游拒绝（HTTP {upstream.get('status')}）"
    checks.append(_check(
        "llm.upstream", group="llm",
        status=upstream_status,
        summary=upstream_summary,
        detail=(f"reason={upstream.get('reason')}；excerpt={upstream.get('excerpt') or '无'}"
                if key_present else "未发起请求"),
        how_to_fix=("检查 Key 是否有效、订阅是否激活、模型是否有权限；"
                    "这一步失败时不得用 Mock 结果代替真实验收"
                    if key_present and not upstream_ok else
                    "先配 LLM_API_KEY" if not key_present else ""),
        required=key_present,
    ))

    # ---- TTS
    tts_engine = str(cfg.get("tts_engine") or "")
    if tts_engine == "silent":
        tts_check = _check("tts.engine", group="tts", status="missing",
                           summary="TTS_ENGINE=silent：不会真正合成音频",
                           how_to_fix="把 TTS_ENGINE 设为 stepaudio 或 edge_tts")
    elif tts_engine == "stepaudio":
        if not key_present:
            tts_check = _check("tts.engine", group="tts", status="skip", required=False,
                               summary="stepaudio 复用 LLM 凭据；当前无 Key",
                               how_to_fix="先配 LLM_API_KEY")
        elif upstream_ok:
            tts_check = _check("tts.engine", group="tts", status="ok",
                               summary="stepaudio TTS 配置就绪（与 LLM 同一出口）")
        else:
            tts_check = _check("tts.engine", group="tts", status="blocked",
                               summary="stepaudio TTS 依赖的上游不可用",
                               detail=upstream_summary,
                               how_to_fix="先修上游；TTS 与 LLM 复用 base_url/key")
    elif tts_engine == "edge_tts":
        edge = deps["edge_probe"]()
        if deps["find_spec"]("edge_tts") is None:
            tts_check = _check("tts.engine", group="tts", status="missing",
                               summary="edge_tts 包未安装",
                               how_to_fix="uv sync（edge-tts 在 uv.lock 里）")
        elif not edge.get("reachable"):
            tts_check = _check("tts.engine", group="tts", status="blocked",
                               summary="edge_tts 需要的外网不可达",
                               detail=redact(edge.get("detail"), env),
                               how_to_fix="检查网络/代理后重试；不要用 Mock 音频代替")
        else:
            tts_check = _check("tts.engine", group="tts", status="ok",
                               summary="edge_tts 可用且外网可达")
    else:
        tts_check = _check("tts.engine", group="tts", status="missing",
                           summary=f"未知 TTS_ENGINE={tts_engine!r}",
                           how_to_fix="检查 .env 的 TTS_ENGINE")
    checks.append(tts_check)

    # ---- ASR
    asr_engine = str(cfg.get("asr_engine") or "")
    if asr_engine == "stepaudio":
        if not key_present:
            asr_check = _check("asr.engine", group="asr", status="skip", required=False,
                               summary="stepaudio ASR 复用 LLM 凭据；当前无 Key",
                               how_to_fix="先配 LLM_API_KEY")
        elif upstream_ok:
            asr_check = _check("asr.engine", group="asr", status="ok",
                               summary=f"stepaudio ASR 配置就绪（{cfg.get('asr_model_id')}）")
        else:
            asr_check = _check("asr.engine", group="asr", status="blocked",
                               summary="stepaudio ASR 依赖的上游不可用",
                               detail=upstream_summary,
                               how_to_fix="先修上游；ASR 与 LLM 复用 base_url/key")
    elif asr_engine == "whisper":
        if deps["find_spec"]("faster_whisper") is None:
            asr_check = _check("asr.engine", group="asr", status="missing",
                               summary="faster-whisper 未安装",
                               how_to_fix="uv sync（faster-whisper 在 uv.lock 里）")
        else:
            whisper = deps["whisper_probe"](cfg, env, root)
            asr_check = _check(
                "asr.engine", group="asr",
                status="ok" if whisper.get("available") else "missing",
                summary=(f"本地 whisper 模型已缓存（{cfg.get('asr_model')}）"
                         if whisper.get("available") else
                         f"本地 whisper 模型未下载（{cfg.get('asr_model')}）"),
                detail=str(whisper.get("detail") or ""),
                how_to_fix=("" if whisper.get("available") else
                            "首次运行会自动下载（需联网）；或预先 "
                            f"huggingface-cli download Systran/faster-whisper-{cfg.get('asr_model')}"),
            )
    else:
        asr_check = _check("asr.engine", group="asr", status="missing",
                           summary=f"未知 ASR_ENGINE={asr_engine!r}",
                           how_to_fix="检查 .env 的 ASR_ENGINE")
    checks.append(asr_check)

    mic = deps["mic_probe"]()
    checks.append(_check(
        "asr.input", group="asr",
        status="ok" if mic.get("available") else "warn",
        summary=(f"检测到 {mic.get('count')} 个麦克风输入设备；用 --mic 可做真人语音"
                 if mic.get("available") else
                 "没有可用麦克风；真人 ASR 需 --wav <录音>"),
        detail=redact(mic.get("error") or "", env),
        how_to_fix="" if mic.get("available") else "接一个麦克风，或用 --wav 指向现成录音",
        required=False,
    ))

    # ---- 端口与落盘
    checks.append(_check(
        "env.ports", group="io", status="info", required=False,
        summary="用户服务端口状态（本脚本只用独立端口，不重启 8000/8010）",
        detail=_fmt_ports(),
    ))

    evidence_ok = False
    evidence_error = ""
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        probe = out_dir / ".doctor-write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        evidence_ok = True
    except OSError as exc:
        evidence_error = str(exc)
    checks.append(_check(
        "io.evidence", group="io",
        status="ok" if evidence_ok else "missing",
        summary="证据目录可写" if evidence_ok else "证据目录不可写",
        detail=str(out_dir) + (f"；{evidence_error}" if evidence_error else ""),
        how_to_fix="" if evidence_ok else "检查 docs/evidence 的写权限或换 --out",
    ))

    sandbox_ok = False
    sandbox_error = ""
    try:
        with tempfile.TemporaryDirectory(prefix="xiaoxi-live-accept-probe-") as tmp:
            Path(tmp, "memory.db").write_text("", encoding="utf-8")
        sandbox_ok = True
    except OSError as exc:
        sandbox_error = str(exc)
    checks.append(_check(
        "io.sandbox", group="io",
        status="ok" if sandbox_ok else "missing",
        summary="可创建临时 DB 沙盘" if sandbox_ok else "无法创建临时 DB 沙盘",
        detail=sandbox_error,
        how_to_fix="" if sandbox_ok else "检查临时目录权限 / 磁盘空间",
    ))

    config_snapshot = {
        "llm_provider": cfg.get("llm_provider"),
        "llm_base_url": cfg.get("llm_base_url"),
        "llm_model": cfg.get("llm_model"),
        "llm_api_key_present": key_present,
        "tts_engine": tts_engine,
        "asr_engine": asr_engine,
        "asr_model": cfg.get("asr_model"),
        "asr_model_id": cfg.get("asr_model_id"),
    }
    if cfg.get("config_import_error"):
        config_snapshot["config_import_error"] = cfg["config_import_error"]
    report = {
        "schema": "xiaoxi.live_acceptance.doctor/v1",
        "generated_at": _now(deps.get("now")() if deps.get("now") else None).isoformat(timespec="seconds"),
        "root": str(root),
        "python": {"executable": sys.executable, "venv": str(venv_py), "using_venv": same_python},
        "config": config_snapshot,
        "upstream": upstream if key_present else None,
        "checks": checks,
        "readiness": assess_readiness(checks),
    }
    return report


def render_doctor_md(report: Mapping[str, Any]) -> str:
    command = report.get("command") or {}
    argv = " ".join(command.get("argv") or [])
    lines = [
        "# 真环境验收 · 环境体检",
        "",
        f"- 生成：{report.get('generated_at')}",
        f"- 命令：`{argv or '—'}`（耗时 {report.get('duration_ms', '—')}ms）",
        f"- 根目录：`{report.get('root')}`",
        f"- 解释器：`{report.get('python', {}).get('executable')}`",
        "",
    ]
    readiness = report.get("readiness") or {}
    ready = bool(readiness.get("ready"))
    lines.append(f"## 结论：{'可以开跑 live' if ready else '未就绪（还差条件）'}")
    lines.append("")
    if ready:
        lines.append("必需项全部为 ok。下一步：`python scripts/live_acceptance.py --mic`（或 `--wav`）。")
    else:
        lines.append("**未开跑 live。Mock 通过不等于真实通过；不要用 Mock 结果补这里缺的项。**")
    lines.append("")
    lines.append("## 还差什么")
    lines.append("")
    missing = readiness.get("missing") or []
    if not missing:
        lines.append("无。")
    else:
        lines.append("| 检查项 | 现状 | 补法 |")
        lines.append("| --- | --- | --- |")
        for item in missing:
            fix = item.get("how_to_fix") or "—"
            lines.append(f"| `{item['id']}` | {item['summary']} | {fix} |")
    lines.append("")
    lines.append("## 检查明细")
    lines.append("")
    lines.append("| 检查项 | 必需 | 状态 | 说明 |")
    lines.append("| --- | --- | --- | --- |")
    for item in report.get("checks") or []:
        detail = item.get("detail") or ""
        summary = item.get("summary") or ""
        text = f"{summary}（{detail}）" if detail else summary
        lines.append(f"| `{item['id']}` | {'是' if item['required'] else '否'} | {item['status']} | {text} |")
    lines.append("")
    lines.append("## Mock 边界")
    lines.append("")
    lines.append("- 无 `LLM_API_KEY` 时服务走 Mock：这只证明代码路径能跑，不证明真实模型、真实语音或真实记忆质量。")
    lines.append("- Key 存在但上游返回 400/401/402/403 时，本报告判“被上游拒绝”，不是“有 Key 就通过”。")
    lines.append("- 体检只做一次 `max_tokens=1` 的上游探测；真实对话/ASR/TTS/记忆检查点是 live 阶段的事。")
    lines.append("")
    return "\n".join(lines)


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _write_doctor(out_dir: Path, report: Mapping[str, Any]) -> None:
    write_text(out_dir / "doctor.json", json.dumps(report, ensure_ascii=False, indent=2))
    write_text(out_dir / "doctor.md", render_doctor_md(report))


def _update_index(out_dir: Path) -> None:
    """证据根目录的索引：列出每次 run 的结论，不覆盖历史。"""
    runs = sorted(p for p in Path(out_dir).glob("run-*") if p.is_dir())
    lines = [
        "# 真环境验收 · 证据索引",
        "",
        "每条 run 都有独立的 `doctor.md` / `run.md`；本文件只做索引，结论以对应 run 为准。",
        "",
        "| run | Key | 体检 | live | 生成时间 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for run in runs:
        key_state = "—"
        doctor_state = "—"
        live_state = "—"
        generated = "—"
        doctor_path = run / "doctor.json"
        if doctor_path.is_file():
            try:
                doctor = json.loads(doctor_path.read_text(encoding="utf-8"))
                key_state = "有" if doctor.get("config", {}).get("llm_api_key_present") else "无"
                doctor_state = "ready" if doctor.get("readiness", {}).get("ready") else "not-ready"
                generated = doctor.get("generated_at") or "—"
            except json.JSONDecodeError:
                doctor_state = "损坏"
        run_path = run / "run.json"
        if run_path.is_file():
            try:
                live_state = json.loads(run_path.read_text(encoding="utf-8")).get("overall", "—")
            except json.JSONDecodeError:
                live_state = "损坏"
        lines.append(f"| `{run.name}` | {key_state} | {doctor_state} | {live_state} | {generated} |")
    lines.append("")
    write_text(Path(out_dir) / "README.md", "\n".join(lines))


# ---------------------------------------------------------------- live 检查点


def _load_cfg() -> dict[str, Any]:
    """从 server.config 取只读快照；Key 只转成布尔，不留在报告里。"""
    try:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        from server import config as sc

        settings = sc.settings
        return {
            "llm_provider": settings.llm_provider,
            "llm_base_url": settings.llm_base_url,
            "llm_model": settings.llm_model,
            "llm_api_key_present": bool(settings.llm_api_key),
            "tts_engine": settings.tts_engine,
            "asr_engine": settings.asr_engine,
            "asr_model": settings.asr_model,
            "asr_model_id": settings.asr_model_id,
            "asr_stream_url": getattr(settings, "asr_stream_url", ""),
        }
    except Exception as exc:
        return {
            "llm_provider": os.getenv("LLM_PROVIDER", ""),
            "llm_base_url": os.getenv("LLM_BASE_URL", ""),
            "llm_model": os.getenv("LLM_MODEL", ""),
            "llm_api_key_present": bool(os.getenv("LLM_API_KEY")),
            "tts_engine": os.getenv("TTS_ENGINE", ""),
            "asr_engine": os.getenv("ASR_ENGINE", ""),
            "asr_model": os.getenv("ASR_MODEL", ""),
            "asr_model_id": os.getenv("ASR_MODEL_ID", ""),
            "asr_stream_url": os.getenv("ASR_STREAM_URL", ""),
            "config_import_error": redact(str(exc), {}),
        }


def _assert(name: str, ok: bool, detail: str = "") -> dict[str, Any]:
    return {"name": name, "ok": bool(ok), "detail": detail}


def _checkpoint(check_id: str, title: str, *, status: str, started: float,
                assertions: list[dict[str, Any]], evidence: dict[str, Any] | None = None,
                repro: str = "", note: str = "") -> dict[str, Any]:
    return {
        "id": check_id,
        "title": title,
        "status": status,
        "duration_ms": int((time.perf_counter() - started) * 1000),
        "assertions": assertions,
        "evidence": evidence or {},
        "repro": repro,
        "note": note,
    }


def _summarize_event(event: Mapping[str, Any]) -> dict[str, Any]:
    out = dict(event)
    if isinstance(out.get("data"), str):
        out["data"] = f"<base64 {len(out['data'])} chars>"
    if isinstance(out.get("audio"), str):
        out["audio"] = f"<base64 {len(out['audio'])} chars>"
    return out


async def _collect_turn(ws, *, timeout: float) -> tuple[list[dict[str, Any]], str, float | None, bool]:
    events: list[dict[str, Any]] = []
    text = ""
    first_delta_ms: float | None = None
    started = time.perf_counter()
    timed_out = False
    try:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=timeout)
            event = json.loads(raw)
            events.append(event)
            kind = event.get("type")
            if kind == "text_delta":
                if first_delta_ms is None:
                    first_delta_ms = (time.perf_counter() - started) * 1000
                text += event.get("delta") or ""
            elif kind == "text":
                text = event.get("text") or text
            elif kind == "done":
                if event.get("text"):
                    text = event["text"]
                return events, text, first_delta_ms, timed_out
            elif kind == "interrupted":
                return events, text, first_delta_ms, timed_out
            elif kind == "error" and event.get("terminal"):
                return events, text, first_delta_ms, timed_out
    except asyncio.TimeoutError:
        timed_out = True
        return events, text, first_delta_ms, timed_out


async def _ws_ready(ws, *, timeout: float = 10.0) -> dict[str, Any]:
    return json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))


def _audio_bytes(events: list[Mapping[str, Any]]) -> list[bytes]:
    chunks: list[bytes] = []
    for event in events:
        if event.get("type") == "audio" and event.get("data"):
            try:
                chunks.append(base64.b64decode(event["data"]))
            except (ValueError, TypeError):
                continue
    return chunks


def _audio_ext(data: bytes) -> str:
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "wav"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "mp3"
    return "bin"


def _pcm16_from_wav(path: Path) -> tuple[bytes, int, dict[str, Any]]:
    """读 WAV，重采样到 16k 单声道 PCM16；返回 (pcm, rate, info)。"""
    import numpy as np

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from server.speech.asr import decode_wav, trim_silence

    audio, rate = decode_wav(Path(path).read_bytes())
    if rate != 16000 and audio.size > 1:
        duration = audio.size / rate
        target = max(1, int(duration * 16000))
        audio = np.interp(np.linspace(0, audio.size - 1, target), np.arange(audio.size), audio)
        rate = 16000
    audio = trim_silence(audio)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio))) if audio.size else 0.0)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    return pcm, rate, {"samples": int(audio.size), "duration_ms": int(audio.size / rate * 1000),
                       "peak": round(peak, 4), "rms": round(rms, 4)}


def _record_mic(path: Path, *, seconds: float) -> dict[str, Any]:
    import numpy as np
    import sounddevice as sd
    import wave

    rate = 16000
    frames = int(seconds * rate)
    data = sd.rec(frames, samplerate=rate, channels=1, dtype="float32")
    sd.wait()
    audio = np.asarray(data, dtype=np.float32).reshape(-1)
    peak = float(np.max(np.abs(audio))) if audio.size else 0.0
    rms = float(np.sqrt(np.mean(np.square(audio))) if audio.size else 0.0)
    pcm = (np.clip(audio, -1, 1) * 32767).astype("<i2").tobytes()
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(pcm)
    return {"duration_ms": int(audio.size / rate * 1000), "peak": round(peak, 4),
            "rms": round(rms, 4), "rate": rate}


def _norm_text(text: str) -> str:
    return re.sub(r"[\s，。！？、,.!?：“”\"'‘’；;：:（）()\[\]-]", "", text or "")


def _similarity(expected: str, actual: str) -> float:
    left, right = _norm_text(expected), _norm_text(actual)
    if not left or not right:
        return 0.0
    return difflib.SequenceMatcher(None, left, right).ratio()


async def _drive_voice_turn(ws, pcm: bytes) -> tuple[list[dict[str, Any]], str, float | None, bool]:
    await ws.send(json.dumps({"type": "start"}))
    for start in range(0, len(pcm), 3200):
        await ws.send(pcm[start:start + 3200])
        await asyncio.sleep(0.01)
    await ws.send(json.dumps({"type": "audio_end"}))
    return await _collect_turn(ws, timeout=180.0)


class _HttpLog:
    """记录每个 REST 调用的命令、状态、耗时与脱敏摘录（证据用）。"""

    def __init__(self, base_url: str, env: Mapping[str, str]) -> None:
        self.base_url = base_url
        self.env = env
        self.calls: list[dict[str, Any]] = []

    def request(self, method: str, path: str, **kwargs: Any) -> Any:
        import httpx

        started = time.perf_counter()
        with httpx.Client(base_url=self.base_url, timeout=30) as client:
            response = client.request(method, path, **kwargs)
        self.calls.append({
            "command": f"{method} {self.base_url}{path}",
            "status": response.status_code,
            "duration_ms": int((time.perf_counter() - started) * 1000),
            "excerpt": redact(response.text, self.env),
        })
        response.raise_for_status()
        return response.json()


def _start_server(root: Path, env: Mapping[str, str], *, port: int,
                  sandbox: Path, log_dir: Path) -> tuple[subprocess.Popen, Any, Any]:
    # 继承完整父环境（PATH/SystemRoot/TEMP 等），再叠调用方显式给定的覆盖值。
    server_env = {**os.environ, **dict(env)}
    server_env.update({
        "DB_PATH": str(sandbox / "memory.db"),
        "APP_DATA_DIR": str(sandbox),
        "TEST_SENTINEL": "1",
        "PORT": str(port),
        "PYTHONUNBUFFERED": "1",
        "HF_HUB_DISABLE_XET": "1",
    })
    stdout = open(log_dir / "server.stdout.log", "w", encoding="utf-8")
    stderr = open(log_dir / "server.stderr.log", "w", encoding="utf-8")
    cmd = [sys.executable, "-m", "uvicorn", "server.app:app",
           "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    proc = subprocess.Popen(cmd, cwd=str(root), env=server_env,
                            stdout=stdout, stderr=stderr)
    return proc, stdout, stderr


def _wait_health(base_url: str, *, timeout: float = 60.0) -> dict[str, Any] | None:
    import httpx

    deadline = time.perf_counter() + timeout
    while time.perf_counter() < deadline:
        try:
            with httpx.Client(base_url=base_url, timeout=5) as client:
                response = client.get("/api/health")
                if response.status_code == 200:
                    return response.json()
        except httpx.HTTPError:
            pass
        time.sleep(0.4)
    return None


def _tail(path: Path, lines: int = 30) -> str:
    try:
        text = Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return "\n".join(text.splitlines()[-lines:])[:4000]


def _stop_server(proc: subprocess.Popen, *, timeout: float = 10.0) -> None:
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass


async def run_live(
    *,
    root: Path,
    env: Mapping[str, str],
    cfg: Mapping[str, Any],
    out_dir: Path,
    args: argparse.Namespace,
    deps: Mapping[str, Any],
) -> dict[str, Any]:
    """体检通过后的真实链路检查。失败时保留可复现命令与日志摘录。"""
    started_at = _now().isoformat(timespec="seconds")
    checkpoints: list[dict[str, Any]] = []
    server_info: dict[str, Any] = {}
    sandbox = Path(tempfile.mkdtemp(prefix="xiaoxi-live-accept-"))
    proc: subprocess.Popen | None = None
    stdout = stderr = None
    overall = "fail"
    try:
        port = _free_port()
        base_url = f"http://127.0.0.1:{port}"
        ws_url = f"ws://127.0.0.1:{port}/ws"
        server_info = {
            "port": port,
            "base_url": base_url,
            "sandbox": str(sandbox),
            "db_path": str(sandbox / "memory.db"),
            "command": (f"$env:DB_PATH='{sandbox / 'memory.db'}'; $env:APP_DATA_DIR='{sandbox}'; "
                        f"$env:TEST_SENTINEL='1'; .\\.venv\\Scripts\\python.exe -m uvicorn "
                        f"server.app:app --host 127.0.0.1 --port {port}"),
        }

        t0 = time.perf_counter()
        proc, stdout, stderr = _start_server(root, env, port=port, sandbox=sandbox, log_dir=out_dir)
        health = _wait_health(base_url, timeout=60)
        if not health:
            assertions = [
                _assert("沙盘服务在 60s 内健康", False,
                        f"stderr 尾部：\n{_tail(out_dir / 'server.stderr.log')}"),
            ]
            checkpoints.append(_checkpoint(
                "L0.server", "独立端口沙盘服务", status="fail", started=t0,
                assertions=assertions,
                evidence={"health": None, "server": server_info},
                repro=server_info["command"] + "，然后 curl /api/health",
            ))
            overall = "fail"
            return {"schema": "xiaoxi.live_acceptance.run/v1", "started_at": started_at,
                    "server": server_info, "checkpoints": checkpoints, "overall": overall}

        offline = bool(health.get("llm", {}).get("offline"))
        assertions = [
            _assert("健康检查返回 ok", bool(health.get("ok")), json.dumps(health, ensure_ascii=False)[:600]),
            _assert("服务不是 Mock 模式", not offline,
                    "health.llm.offline=true：无 Key/Key 未生效，拒绝用 Mock 冒充实测"),
            _assert("TTS 引擎与配置一致", health.get("tts", {}).get("engine") == cfg.get("tts_engine"),
                    f"health={health.get('tts')}；config={cfg.get('tts_engine')}"),
            _assert("ASR 引擎与配置一致", health.get("asr", {}).get("engine") == cfg.get("asr_engine"),
                    f"health={health.get('asr')}；config={cfg.get('asr_engine')}"),
        ]
        l0_status = "pass" if all(item["ok"] for item in assertions) else "fail"
        checkpoints.append(_checkpoint(
            "L0.server", "独立端口沙盘服务", status=l0_status, started=t0,
            assertions=assertions, evidence={"health": health, "server": server_info},
            repro=server_info["command"] + "，然后 curl /api/health",
        ))
        if l0_status != "pass":
            overall = "fail"
            return {"schema": "xiaoxi.live_acceptance.run/v1", "started_at": started_at,
                    "server": server_info, "checkpoints": checkpoints, "overall": overall}

        # ---- B1 真实对话
        t1 = time.perf_counter()
        dialog_events: list[dict[str, Any]] = []
        dialog_text = ""
        first_delta_ms: float | None = None
        dialog_error = ""
        try:
            persona = _HttpLog(base_url, env).request("GET", "/api/persona")
            voices = list(persona.get("voices") or [])
            async with __import__("websockets").connect(ws_url, max_size=16 * 1024 * 1024,
                                                        open_timeout=15) as ws:
                ready = await _ws_ready(ws)
                await ws.send(json.dumps({"type": "text", "text": "我今天有点累，随便说两句吧。"}))
                dialog_events, dialog_text, first_delta_ms, timed_out = await _collect_turn(ws, timeout=120.0)
        except Exception as exc:
            timed_out = False
            dialog_error = redact(str(exc), env)
            voices = []
        emotions = [e.get("emotion") for e in dialog_events if e.get("type") == "emotion"]
        emotion = emotions[-1] if emotions else None
        audio_chunks = _audio_bytes(dialog_events)
        reply_norm = _norm_text(dialog_text)
        from server.llm import MOCK_QUESTION_REPLY, MOCK_REPLIES
        mock_norms = {_norm_text(item) for item in (*MOCK_REPLIES, MOCK_QUESTION_REPLY)}
        assertions = [
            _assert("收到 ready", True, "已收到 ready 帧"),
            _assert("没有超时", not timed_out, "120s 内收到 done/terminal error"),
            _assert("没有连接级异常", not dialog_error, dialog_error),
            _assert("回复非空", bool(dialog_text.strip()), repr(dialog_text[:200])),
            _assert("回复不是 Mock 固定句", bool(dialog_text.strip()) and reply_norm not in mock_norms,
                    repr(dialog_text[:120]),
                    ),
            _assert("emotion 在合法集合内", emotion in voices,
                    f"emotion={emotion!r}；合法={voices}"),
        ]
        b1_status = "pass" if all(item["ok"] for item in assertions) else "fail"
        checkpoints.append(_checkpoint(
            "B1.dialogue", "一轮真实对话（真实 LLM）", status=b1_status, started=t1,
            assertions=assertions,
            evidence={
                "request": "WS send {type:text,text:我今天有点累，随便说两句吧。}",
                "events": [_summarize_event(e) for e in dialog_events],
                "reply": dialog_text,
                "emotion": emotion,
                "first_text_delta_ms": round(first_delta_ms, 1) if first_delta_ms is not None else None,
                "audio_chunks": len(audio_chunks),
            },
            repro=f"{server_info['command']}；再按 scripts/live_acceptance.py 的 B1 步骤发 WS 文本帧",
        ))
        write_text(out_dir / "dialog-events.json", json.dumps([_summarize_event(e) for e in dialog_events],
                                                              ensure_ascii=False, indent=2))

        # ---- B3 真实 TTS（复用 B1 的音频帧，等价于服务端真实合成）
        t3 = time.perf_counter()
        total_audio = sum(len(item) for item in audio_chunks)
        paths = []
        for index, data in enumerate(audio_chunks):
            ext = _audio_ext(data)
            path = out_dir / f"tts-chunk-{index:02d}.{ext}"
            path.write_bytes(data)
            paths.append(str(path))
        assertions = [
            _assert("收到 audio 帧", bool(audio_chunks), f"chunks={len(audio_chunks)}"),
            _assert("音频总字节 > 2000", total_audio > 2000, f"bytes={total_audio}"),
            _assert("至少一个音频容器可识别", any(_audio_ext(b) != "bin" for b in audio_chunks),
                    f"exts={[_audio_ext(b) for b in audio_chunks[:3]]}"),
        ]
        played = False
        if args.play and audio_chunks and deps["which"]("ffplay"):
            try:
                results = [
                    subprocess.run([deps["which"]("ffplay"), "-nodisp", "-autoexit",
                                    "-loglevel", "error", path], timeout=30, check=False)
                    for path in paths[:3]
                ]
                played = all(item.returncode == 0 for item in results)
            except (OSError, subprocess.TimeoutExpired):
                played = False
        assertions.append(_assert("需要出声（--play）", played if args.play else True,
                                  "已用 ffplay 播放" if played else
                                  ("未加 --play，只落盘；听感需人工确认" if not args.play else "ffplay 不可用")))
        b3_status = "pass" if all(item["ok"] for item in assertions) else "fail"
        checkpoints.append(_checkpoint(
            "B3.tts", "真实 TTS 合成（服务端输出音频）", status=b3_status, started=t3,
            assertions=assertions,
            evidence={"audio_bytes": total_audio, "chunks": paths, "played": played},
            repro=server_info["command"] + "；发一轮文本，收 audio 帧后 base64 解码",
            note="“听感是否自然/是否像她”属于人工判断，不由脚本冒充。",
        ))

        # ---- B2 真实 ASR
        t2 = time.perf_counter()
        source = None
        pcm = b""
        expect = args.expect_text
        audio_info: dict[str, Any] = {}
        input_error = ""
        if args.wav:
            source = str(Path(args.wav).resolve())
            try:
                pcm, rate, audio_info = _pcm16_from_wav(Path(args.wav))
                shutil.copy2(args.wav, out_dir / "asr-input.wav")
            except Exception as exc:
                input_error = redact(str(exc), env)
        elif args.mic:
            source = "microphone"
            try:
                print(f"\n[麦克风] 请朗读：{DEFAULT_ASR_PHRASE}（{args.mic_seconds:g} 秒）")
                audio_info = _record_mic(out_dir / "mic-input.wav", seconds=args.mic_seconds)
                pcm, rate, _ = _pcm16_from_wav(out_dir / "mic-input.wav")
                expect = expect or DEFAULT_ASR_PHRASE
            except Exception as exc:
                input_error = redact(str(exc), env)
        else:
            source = None

        if not source:
            checkpoints.append(_checkpoint(
                "B2.asr", "一次真实 ASR（真人语音）", status="skip", started=t2,
                assertions=[_assert("提供了真人音频来源", False, "未给 --mic 或 --wav")],
                evidence={"source": None},
                repro="python scripts\\live_acceptance.py --mic 或 --wav <录音.wav> --expect-text <原句>",
                note="缺省不拿 TTS 合成音冒充真人语音；这一项必须由人提供输入。",
            ))
        elif input_error:
            checkpoints.append(_checkpoint(
                "B2.asr", "一次真实 ASR（真人语音）", status="fail", started=t2,
                assertions=[_assert("音频可读取", False, input_error)],
                evidence={"source": source},
                repro="换一个 16-bit PCM WAV，或确认麦克风权限后重跑",
            ))
        else:
            asr_text = ""
            reply_text = ""
            asr_error = ""
            degraded = None
            try:
                async with __import__("websockets").connect(ws_url, max_size=16 * 1024 * 1024,
                                                            open_timeout=15) as ws:
                    await _ws_ready(ws)
                    events, reply_text, _, timed_out = await _drive_voice_turn(ws, pcm)
                asr_event = next((e for e in events if e.get("type") == "asr"), None)
                if asr_event:
                    asr_text = asr_event.get("text") or ""
                degraded = next((e for e in events if e.get("type") == "error" and e.get("recoverable")), None)
            except Exception as exc:
                asr_error = redact(str(exc), env)
                events = []
            ratio = _similarity(expect, asr_text) if expect else None
            assertions = [
                _assert("收到 asr 帧", bool(asr_text), f"asr={asr_text!r}"),
                _assert("ASR 没有降级", degraded is None,
                        f"degraded={degraded.get('message') if degraded else '无'}"),
                _assert("没有连接级异常", not asr_error, asr_error),
                _assert("语音轮有真实回复", bool(reply_text.strip()), repr(reply_text[:200])),
            ]
            if expect:
                assertions.append(_assert(
                    f"转写相似度 ≥ {args.min_asr_ratio:g}",
                    bool(ratio is not None and ratio >= args.min_asr_ratio),
                    f"expected={expect!r}；actual={asr_text!r}；ratio={ratio:.3f}" if ratio is not None
                    else f"expected={expect!r}；actual={asr_text!r}；ratio=0.000",
                ))
            b2_status = "pass" if all(item["ok"] for item in assertions) else "fail"
            checkpoints.append(_checkpoint(
                "B2.asr", "一次真实 ASR（真人语音）", status=b2_status, started=t2,
                assertions=assertions,
                evidence={"source": source, "audio": audio_info, "asr_text": asr_text,
                          "reply": reply_text, "similarity": round(ratio, 3) if ratio is not None else None},
                repro=("python scripts\\live_acceptance.py --wav <录音.wav> "
                       f"--expect-text \"{expect}\"" if args.wav else
                       "python scripts\\live_acceptance.py --mic"),
                note="转写相似度只验“听对了这句话”；音色/口音鲁棒性需更多样本。",
            ))

        # ---- B4 记忆写入 → 检索命中 → 硬删除
        t4 = time.perf_counter()
        memory_log = _HttpLog(base_url, env)
        memory_error = ""
        memory_id = None
        baseline = 0
        key = ""
        try:
            listed = memory_log.request("GET", "/api/memories")
            if not listed.get("enabled", True):
                memory_log.request("POST", "/api/memory/toggle", json={"enabled": True})
                listed = memory_log.request("GET", "/api/memories")
            baseline = int(listed.get("count") or 0)
            key = f"live-accept:{int(time.time())}"
            value = "验收记忆：用户周六要去修自行车。"
            added = memory_log.request("POST", "/api/memory/manual", json={
                "kind": "episode", "subject": "user", "key": key, "value": value,
                "importance": 0.85, "confidence": 0.9,
            })
            memory_id = added.get("id")
            after_add = memory_log.request("GET", "/api/memories")
            context = memory_log.request("GET", "/api/memories/context",
                                         params={"query": "周六修自行车", "subject": "user", "limit": 10})
        except Exception as exc:
            memory_error = redact(str(exc), env)
            after_add = {}
            context = {}
        found = bool(context.get("found"))
        context_text = str(context.get("context") or "")
        assertions = [
            _assert("写入返回 id", isinstance(memory_id, int), f"id={memory_id}"),
            _assert("写入后计数 +1", int(after_add.get("count") or -1) == baseline + 1,
                    f"baseline={baseline}；after={after_add.get('count')}"),
            _assert("检索 found=true", found, f"found={found}"),
            _assert("检索上下文含写入内容", (key in context_text) or ("修自行车" in context_text),
                    context_text[:300]),
            _assert("没有 HTTP 异常", not memory_error, memory_error),
        ]
        b4_status = "pass" if all(item["ok"] for item in assertions) else "fail"
        delete_ok = None
        if isinstance(memory_id, int):
            try:
                memory_log.request("DELETE", f"/api/memories/{memory_id}")
                final = memory_log.request("GET", "/api/memories")
                delete_ok = int(final.get("count") or -1) == baseline
            except Exception as exc:
                memory_error = memory_error or redact(str(exc), env)
                delete_ok = False
        checkpoints.append(_checkpoint(
            "B4.memory", "记忆写入 → 检索命中 → 硬删除", status=b4_status, started=t4,
            assertions=assertions,
            evidence={**memory_log.calls[-8:], "baseline": baseline, "key": key,
                      "found": found, "deleted_ok": delete_ok,
                      "context_excerpt": context_text[:400]},
            repro=server_info["command"] + "；按 B4 的 REST 序列重放（库是临时沙盘）",
            note="检索是确定性层，不依赖模型怎么措辞；模型有没有“用上”记忆属于 eval 层。",
        ))

        statuses = [cp["status"] for cp in checkpoints]
        if "fail" in statuses:
            overall = "fail"
        elif "skip" in statuses:
            overall = "partial"
        else:
            overall = "pass"
        return {"schema": "xiaoxi.live_acceptance.run/v1", "started_at": started_at,
                "server": server_info, "checkpoints": checkpoints, "overall": overall}
    finally:
        if proc is not None:
            _stop_server(proc)
        for handle in (stdout, stderr):
            try:
                handle.close()
            except Exception:
                pass
        if not getattr(args, "keep_sandbox", False):
            shutil.rmtree(sandbox, ignore_errors=True)


def render_run_md(report: Mapping[str, Any]) -> str:
    command = report.get("command") or {}
    argv = " ".join(command.get("argv") or [])
    lines = [
        "# 真环境验收 · live 检查点",
        "",
        f"- 开始：{report.get('started_at')}",
        f"- 命令：`{argv or '—'}`（耗时 {report.get('duration_ms', '—')}ms）",
        f"- 结论：**{report.get('overall')}**",
        f"- 沙盘端口：{report.get('server', {}).get('port')}（未碰 8000/8010）",
        f"- 沙盘 DB：`{report.get('server', {}).get('db_path')}`",
        "",
        "| 检查点 | 验什么 | 状态 | 耗时 |",
        "| --- | --- | --- | --- |",
    ]
    for cp in report.get("checkpoints") or []:
        lines.append(f"| `{cp['id']}` | {cp['title']} | {cp['status']} | {cp['duration_ms']}ms |")
    lines.append("")
    for cp in report.get("checkpoints") or []:
        lines.append(f"## {cp['id']} · {cp['title']}")
        lines.append("")
        for item in cp.get("assertions") or []:
            mark = "PASS" if item["ok"] else "FAIL"
            detail = item.get("detail") or ""
            lines.append(f"- [{mark}] {item['name']}" + (f"：{detail}" if detail else ""))
        if cp.get("note"):
            lines.append(f"- 说明：{cp['note']}")
        if cp.get("repro"):
            lines.append(f"- 复现：`{cp['repro']}`")
        lines.append("")
    lines.append("## 结论边界")
    lines.append("")
    lines.append("- `pass` 只代表本文件列出的断言在本次环境为真，不代表音质、性格或长期记忆质量。")
    lines.append("- `partial`（有 skip）不能当完整验收；`fail` 请按对应检查点的复现命令重跑并保留日志。")
    lines.append("- 本次 live 全程使用独立端口与临时 DB；8000/8010 未被重启或改写。")
    lines.append("")
    return "\n".join(lines)


# ---------------------------------------------------------------- CLI


def _default_deps() -> dict[str, Any]:
    return {
        "find_spec": importlib.util.find_spec,
        "which": shutil.which,
        "port_open": port_open,
        "upstream_probe": probe_upstream,
        "mic_probe": probe_mic,
        "whisper_probe": probe_whisper,
        "edge_probe": probe_edge_network,
        "now": datetime.now,
    }


def main(argv: list[str] | None = None, *, env: Mapping[str, str] | None = None,
         cfg: Mapping[str, Any] | None = None,
         deps: Mapping[str, Any] | None = None) -> int:
    parser = argparse.ArgumentParser(description="小溪真环境验收向导（体检 + 真实链路检查点）")
    parser.add_argument("--doctor", action="store_true", help="只做环境体检，不跑 live")
    parser.add_argument("--strict", action="store_true", help="体检未就绪时退出 2")
    parser.add_argument("--out", default="", help="证据目录（默认 docs/evidence/<YYYYMMDD>-live-acceptance）")
    parser.add_argument("--wav", default="", help="现成录音（16-bit PCM WAV），用于真人 ASR")
    parser.add_argument("--mic", action="store_true", help="现场录麦克风，用于真人 ASR")
    parser.add_argument("--mic-seconds", type=float, default=4.0, help="麦克风录制秒数（默认 4）")
    parser.add_argument("--expect-text", default="", help="与录音内容对应的原句；配 --wav 时强烈建议给")
    parser.add_argument("--min-asr-ratio", type=float, default=0.6, help="转写相似度下限（默认 0.6）")
    parser.add_argument("--play", action="store_true", help="用 ffplay 播放 TTS 产物")
    parser.add_argument("--keep-sandbox", action="store_true", help="保留临时 DB 沙盘（排障用）")
    args = parser.parse_args(argv)

    if cfg is None:
        cfg = _load_cfg()
    if env is None:
        env = dict(os.environ)
    deps = {**_default_deps(), **(deps or {})}
    now = _now(deps.get("now")() if deps.get("now") else None)
    out_dir = Path(args.out) if args.out else evidence_root(ROOT, date=now)
    run_dir = _run_dir(out_dir, now=now)

    doctor_started = time.perf_counter()
    doctor = build_doctor(root=ROOT, env=env, cfg=cfg, out_dir=run_dir, deps=deps)
    doctor["command"] = {
        "cwd": str(ROOT),
        "argv": ["scripts/live_acceptance.py", *(argv if argv is not None else sys.argv[1:])],
    }
    doctor["duration_ms"] = int((time.perf_counter() - doctor_started) * 1000)
    _write_doctor(run_dir, doctor)
    readiness = doctor["readiness"]
    print(f"[体检] 证据：{run_dir}")
    print(f"[体检] {'就绪' if readiness['ready'] else '未就绪'}；警告 {len(readiness['warn'])} 项")
    if not readiness["ready"]:
        print("[体检] 还差：")
        for item in readiness["missing"]:
            print(f"  - {item['id']}: {item['summary']} → {item['how_to_fix']}")
        print("[体检] Mock 通过不等于真实通过；这里不会继续跑 live。")

    _update_index(out_dir)

    if args.doctor:
        return 0 if (readiness["ready"] or not args.strict) else 2
    if not readiness["ready"]:
        return 2

    # 显式给了真人输入参数时先 fail fast，避免白跑 B1/B3 才发现输入不可用。
    if args.wav and not Path(args.wav).is_file():
        print(f"[live] --wav 文件不存在：{args.wav}")
        return 2
    if args.mic:
        mic = next((c for c in doctor["checks"] if c["id"] == "asr.input"), None)
        if not mic or mic["status"] != "ok":
            print("[live] --mic 要求可用麦克风：" + (mic["summary"] if mic else "未探测到输入设备"))
            return 2

    print("[live] 体检通过，开始真实链路检查（独立端口 + 临时 DB）…")
    live_started = time.perf_counter()
    report = asyncio.run(run_live(root=ROOT, env=env, cfg=cfg, out_dir=run_dir, args=args, deps=deps))
    report["command"] = {
        "cwd": str(ROOT),
        "argv": ["scripts/live_acceptance.py", *(argv if argv is not None else sys.argv[1:])],
    }
    report["duration_ms"] = int((time.perf_counter() - live_started) * 1000)
    write_text(run_dir / "run.json", json.dumps(report, ensure_ascii=False, indent=2))
    write_text(run_dir / "run.md", render_run_md(report))
    _update_index(out_dir)
    print(f"[live] 结论：{report['overall']}；证据：{run_dir}")
    return 0 if report["overall"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
