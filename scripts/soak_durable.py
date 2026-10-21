"""Long-running chaos/soak harness for the durable sidecar.

    python scripts/soak_durable.py --duration-hours 24
    python scripts/soak_durable.py --duration-seconds 30 --kill-every 8 --concurrency 3

It submits scripted runs at a fixed concurrency, kills the sidecar periodically,
lets the supervisor restart it, and checks each run ends with exactly 3 unique
progress events and 1 artifact. It samples /v1/stats (RSS, heap, handles) and the
SQLite/WAL size into a JSON report.

This is meant to run as a separate overnight process, not inside run_gates.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402

from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402


def hard_kill(pid: int) -> bool:
    if os.name == "nt":
        result = subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)],
                                capture_output=True, text=True)
        return result.returncode == 0
    try:
        os.kill(pid, 9)
        return True
    except OSError:
        return False


def db_bytes(directory: Path) -> int:
    total = 0
    for name in ("agent.sqlite", "agent.sqlite-wal", "agent.sqlite-shm"):
        path = directory / name
        if path.is_file():
            total += path.stat().st_size
    return total


async def wait_endpoint(supervisor: DurableSupervisor, previous: str | None,
                        timeout: float = 60.0) -> str | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = supervisor.current_endpoint()
        if current is not None and current != previous:
            return current
        await asyncio.sleep(0.25)
    return None


async def sample(client: httpx.AsyncClient, endpoint: str, token: str,
                 directory: Path) -> dict:
    headers = {"authorization": f"Bearer {token}"}
    stats = None
    try:
        response = await client.get(f"{endpoint}/v1/stats", headers=headers)
        if response.status_code == 200:
            stats = response.json()
    except Exception:
        pass
    return {"at": time.time(), "db_bytes": db_bytes(directory), "stats": stats}


async def main_async(args) -> int:
    duration = args.duration_seconds or args.duration_hours * 3600.0
    if duration <= 0:
        print("duration must be positive")
        return 2
    directory = Path(tempfile.mkdtemp(prefix="durable-soak-long-"))
    report_path = Path(args.metrics_json) if args.metrics_json else (
        ROOT / "tmp" / f"soak-durable-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    supervisor = DurableSupervisor(
        directory=directory, startup_timeout=40.0, auto_restart=True,
        max_restarts=1000, window_seconds=600.0,
        extra_env={
            "XIAOXI_WORKER_MODE": "script",
            "XIAOXI_STEP_DELAY_MS": str(max(20, args.step_delay_ms)),
        },
    )
    submitted = completed = cancelled = failed = 0
    checks_failed = 0
    kills = 0
    restart_failures = 0
    idempotent_checks = 0
    idempotent_failures = 0
    inflight: dict[int, dict] = {}
    metrics: list[dict] = []
    failures: list[dict] = []
    max_rss = max_db = 0
    endpoint = None
    started = time.monotonic()
    end = started + duration
    next_submit = started
    next_kill = started + args.kill_every if args.kill_every > 0 else float("inf")
    next_sample = started
    headers = {"authorization": f"Bearer {supervisor.token}"}
    try:
        endpoint = await supervisor.start()
        async with httpx.AsyncClient(timeout=20.0) as client:
            while time.monotonic() < end or inflight:
                now = time.monotonic()
                current = supervisor.current_endpoint()
                if current is not None:
                    endpoint = current
                if now >= next_kill and current is not None and args.kill_every > 0:
                    pid = supervisor._process.pid if supervisor._process is not None else None
                    if pid is not None:
                        kills += 1
                        print(f"[soak] kill #{kills} sidecar pid={pid}; inflight={len(inflight)}", flush=True)
                        hard_kill(int(pid))
                        new_endpoint = await wait_endpoint(supervisor, endpoint, timeout=60.0)
                        if new_endpoint is None:
                            restart_failures += 1
                            failures.append({"kind": "restart_failed", "reason": supervisor.reason})
                            print("[soak] restart failed; stopping", flush=True)
                            break
                        endpoint = new_endpoint
                    next_kill = now + args.kill_every
                if now < end and endpoint is not None and now >= next_submit and len(inflight) < args.concurrency:
                    submitted += 1
                    commission_id = f"soak_{int(time.time()*1000)}_{submitted}"
                    try:
                        response = await client.post(f"{endpoint}/v1/runs", headers=headers, json={
                            "commission_id": commission_id,
                            "agent_id": "w1",
                            "packet": {"objective": "长期 soak"},
                        })
                        response.raise_for_status()
                        task_id = int(response.json()["task_id"])
                        inflight[task_id] = {
                            "commission_id": commission_id, "submitted_at": now,
                            "cancel_requested": False,
                        }
                        if args.duplicate_every > 0 and submitted % args.duplicate_every == 0:
                            repeated = await client.post(f"{endpoint}/v1/runs", headers=headers, json={
                                "commission_id": commission_id,
                                "agent_id": "w1",
                                "packet": {"objective": "长期 soak"},
                            })
                            same = (repeated.status_code == 200 and
                                    int(repeated.json().get("task_id") or 0) == task_id)
                            idempotent_checks += 1
                            if not same:
                                idempotent_failures += 1
                                failures.append({"kind": "idempotency", "task_id": task_id})
                        if args.cancel_every > 0 and submitted % args.cancel_every == 0:
                            cancel_target = next(iter(inflight))
                            inflight[cancel_target]["cancel_requested"] = True
                            await client.post(f"{endpoint}/v1/runs/{cancel_target}/cancel",
                                              headers=headers)
                    except Exception as exc:
                        failed += 1
                        failures.append({"kind": "submit", "error": str(exc)[:300]})
                    next_submit = now + max(0.2, args.interval)
                if endpoint is not None:
                    for task_id, item in list(inflight.items()):
                        try:
                            response = await client.get(f"{endpoint}/v1/runs/{task_id}", headers=headers)
                            if response.status_code != 200:
                                continue
                            view = response.json()
                            if view.get("status") != "terminal":
                                if now - item["submitted_at"] > args.task_timeout:
                                    failed += 1
                                    failures.append({"kind": "timeout", "task_id": task_id})
                                    del inflight[task_id]
                                continue
                            events = view.get("events") or []
                            progress = [event for event in events if event.get("type") == "progress"]
                            artifacts = [event for event in events if event.get("type") == "artifact"]
                            outcome = view.get("outcome") or {}
                            if item["cancel_requested"] and outcome.get("status") in ("aborted", "cancelled"):
                                cancelled += 1
                                del inflight[task_id]
                                continue
                            # A cancel that loses the race to completion is fine; it
                            # still counts as a normal completed run.
                            ok = (outcome.get("status") == "completed"
                                  and len(progress) == 3
                                  and len({event.get("step_idx") for event in progress}) == 3
                                  and len(artifacts) == 1)
                            if ok:
                                completed += 1
                            else:
                                failed += 1
                                checks_failed += 1
                                failures.append({"kind": "bad_result", "task_id": task_id,
                                                 "progress": len(progress), "artifacts": len(artifacts),
                                                 "outcome": outcome})
                            del inflight[task_id]
                        except Exception:
                            continue
                if now >= next_sample:
                    if endpoint is not None:
                        item = await sample(client, endpoint, supervisor.token, directory)
                        item.update({"submitted": submitted, "completed": completed,
                                     "cancelled": cancelled, "failed": failed,
                                     "in_flight": len(inflight), "kills": kills,
                                     "idempotent_checks": idempotent_checks,
                                     "idempotent_failures": idempotent_failures})
                        stats = item.get("stats") or {}
                        max_rss = max(max_rss, int(stats.get("rss") or 0))
                        max_db = max(max_db, int(item.get("db_bytes") or 0))
                        metrics.append(item)
                        print(f"[soak] t={int(now-started)}s sub={submitted} done={completed} "
                              f"cancel={cancelled} fail={failed} inflight={len(inflight)} kills={kills} "
                              f"rss={int(stats.get('rss') or 0)//1024//1024}MiB "
                              f"db={int(item.get('db_bytes') or 0)//1024//1024}MiB", flush=True)
                    next_sample = now + args.sample_every
                await asyncio.sleep(0.2)
    finally:
        await supervisor.stop()
    report = {
        "duration_seconds": round(time.monotonic() - started, 1),
        "submitted": submitted, "completed": completed, "cancelled": cancelled,
        "failed": failed, "checks_failed": checks_failed, "kills": kills,
        "restart_failures": restart_failures,
        "idempotent_checks": idempotent_checks,
        "idempotent_failures": idempotent_failures,
        "max_rss_bytes": max_rss, "max_db_bytes": max_db,
        "failures": failures[-50:], "metrics": metrics,
        "directory": str(directory),
    }
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"report": str(report_path), "submitted": submitted,
                      "completed": completed, "cancelled": cancelled,
                      "failed": failed, "kills": kills,
                      "restart_failures": restart_failures,
                      "idempotent_checks": idempotent_checks,
                      "idempotent_failures": idempotent_failures,
                      "max_rss_bytes": max_rss, "max_db_bytes": max_db}, indent=2))
    if not args.keep:
        shutil.rmtree(directory, ignore_errors=True)
    return 0 if failed == 0 and restart_failures == 0 else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-hours", type=float, default=24.0)
    parser.add_argument("--duration-seconds", type=float, default=0.0)
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--interval", type=float, default=5.0, help="submit interval seconds")
    parser.add_argument("--kill-every", type=float, default=1800.0,
                        help="seconds between hard kills; 0 disables")
    parser.add_argument("--cancel-every", type=int, default=20,
                        help="cancel one in-flight run every N submissions; 0 disables")
    parser.add_argument("--duplicate-every", type=int, default=50,
                        help="repeat a create every N submissions to check idempotency; 0 disables")
    parser.add_argument("--step-delay-ms", type=int, default=200)
    parser.add_argument("--task-timeout", type=float, default=300.0)
    parser.add_argument("--sample-every", type=float, default=60.0)
    parser.add_argument("--metrics-json", default="")
    parser.add_argument("--keep", action="store_true")
    args = parser.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
