"""Sandbox CLI worker shim: 受控小任务真子进程执行（T2，issue #21）。

与 claude/dsh 等真机 Worker 不同，这个执行体不接任何模型网关：它是纯标准库
的确定性脚本，用来打通「调度 -> 子进程 -> 遥测 -> 验收 -> 台账记账 ->
commission_update 广播」的全链，给后续 CLI 型 Worker 打样。

沙盒边界（票面要求）：不联网（零网络 import，零外呼）、不删除任何既有文件，
只在本单独立工作目录里新增一个校验产物。

行协议见 docs/PROTOCOL.md §22：stdout 每行一个 JSON 事件。token 事件按 §20
如实上报——本执行体没有模型调用，token 数记 0、ttft 记 0（不编造），
duration_ms 是真实执行耗时，status_code=0 表示非 HTTP 子进程结果。

Environment:
  AQI_BRIEFING_PACKET    briefing packet JSON (set by cli_subproc)
  AQI_COMMISSION_ID      commission id (set by cli_subproc)
  AQI_WORKSPACE_ROOT     workspace root (default: <repo>/data/commissions)
  AQI_OUTDIR             explicit output directory (default: <workdir>/out)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
REPORT_NAME = "verify_report.json"


def emit(event: dict) -> None:
    sys.stdout.write(json.dumps(event, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def clean(text: object, limit: int) -> str:
    return str(text or "").replace("\ufffd", "").strip()[:limit]


def packet() -> dict:
    raw = os.environ.get("AQI_BRIEFING_PACKET") or ""
    if not raw.strip():
        emit({"type": "error", "text": "缺少派单密件（AQI_BRIEFING_PACKET），拒绝执行。"})
        return {}
    try:
        value = json.loads(raw)
    except ValueError:
        emit({"type": "error", "text": "派单密件不是合法 JSON，拒绝执行。"})
        return {}
    if not isinstance(value, dict):
        emit({"type": "error", "text": "派单密件不是对象，拒绝执行。"})
        return {}
    return value


def resolve_workdir() -> Path:
    """每单独立子树：<AQI_WORKSPACE_ROOT>/<commission>-<stamp>/，绝不复用别人的目录。"""
    root = Path(os.environ.get("AQI_WORKSPACE_ROOT") or (ROOT / "data" / "commissions"))
    raw = os.environ.get("AQI_COMMISSION_ID") or "ad-hoc"
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "-_")[:40] or "ad-hoc"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return (root / f"{safe}-{stamp}").resolve()


def objective_digest(pack: dict) -> tuple[str, str]:
    objective = clean(pack.get("objective"), 400) or "未给出目标"
    return objective, hashlib.sha256(objective.encode("utf-8")).hexdigest()


def run(pack: dict, outdir: Path) -> int:
    started = time.time()
    commission_id = clean(os.environ.get("AQI_COMMISSION_ID"), 64) or "ad-hoc"
    objective, objective_sha = objective_digest(pack)
    emit({"type": "progress", "step_idx": 1,
          "text": f"读取派单密件 objective_sha256={objective_sha[:12]}"})
    try:
        outdir.mkdir(parents=True, exist_ok=True)
        report = {
            "commission_id": commission_id,
            "agent_id": "sandbox",
            "attempt": int(pack.get("attempt") or 0),
            "objective": objective,
            "objective_sha256": objective_sha,
            "written_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "status": "verified",
        }
        payload = json.dumps(report, ensure_ascii=False, indent=2)
        target = outdir / REPORT_NAME
        target.write_text(payload + "\n", encoding="utf-8")
    except OSError as exc:
        emit({"type": "error", "text": f"写校验产物失败：{type(exc).__name__}"})
        return 0
    # 回读校验：写进去的文件必须能读回来且指纹一致，这一步才是「校验」本身。
    try:
        readback = target.read_text(encoding="utf-8")
        readback_sha = hashlib.sha256(readback.encode("utf-8")).hexdigest()
        expected_sha = hashlib.sha256((payload + "\n").encode("utf-8")).hexdigest()
    except OSError as exc:
        emit({"type": "error", "text": f"回读校验产物失败：{type(exc).__name__}"})
        return 0
    if readback_sha != expected_sha:
        emit({"type": "error",
              "text": f"回读指纹不一致：写 {expected_sha[:12]} 读 {readback_sha[:12]}"})
        return 0
    emit({"type": "progress", "step_idx": 2,
          "text": f"校验产物已回读，sha256 一致（{readback_sha[:12]}）"})
    emit({
        "type": "token",
        "model": "sandbox-worker",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "ttft_ms": 0,
        "duration_ms": int((time.time() - started) * 1000),
        "status_code": 0,
    })
    emit({
        "type": "artifact",
        "artifact": {
            "summary": f"已在 {target.parent} 生成 {REPORT_NAME} 并回读校验通过"
                       f"（objective_sha256={objective_sha[:12]}）。",
            "intercepted": "沙盒边界：不联网、不删除既有文件，只在本单工作目录内新增一个校验产物。",
            "artifacts": [{"kind": "file", "ref": str(target),
                           "preview": readback[:200]}],
        },
    })
    return 0


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    pack = packet()
    if not pack:
        return 0
    workdir = resolve_workdir()
    outdir = Path(os.environ.get("AQI_OUTDIR") or (workdir / "out")).resolve()
    return run(pack, outdir)


if __name__ == "__main__":
    raise SystemExit(main())
