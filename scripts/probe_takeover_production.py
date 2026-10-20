"""durable 接管本地 Agent 的真机端到端：派单 → 持续监督 → 验收 → 中断恢复。

    python scripts/probe_takeover_production.py
    python scripts/probe_takeover_production.py --dry-run

不是回归：会拉起本机 DeepSeek Harness（danger-full-access，见花名册注释）并调
真实模型，花 token。它回答的是 `probe_takeover_live.py` 回答不了的那三个问题：

  1. 走**生产链路**（`ExecutiveService.delegate` → 选将 → 派单 → 适配器 → sidecar
     → DSH）而不是直接 POST /v1/runs。接管在生产路径上真的通。
  2. 持续监督：DSH 每一步的进度/用量在 Commission 台账与遥测里真的有记录，
     不是"跑完才知道发生了什么"。
  3. 中断恢复：sidecar 进程被硬杀之后，监督器重起它，重启对账拿到 DSH 已经
     写下的成果，而不是判一句"服务重启过，这单要重跑"。

与 `probe_takeover_live.py` 的分工：后者验"sidecar 能拉起本地执行体并收下它的
交差"，前者验"这一单在生产链路上被派出去、被看着、被验收、死了能接回来"。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from server.config import settings  # noqa: E402,F401  (import 即加载 .env)
from server.assistant.durable_supervisor import DurableSupervisor  # noqa: E402
from server.assistant.evaluator import load_roster  # noqa: E402
from server.assistant.executive import ExecutiveService  # noqa: E402

PROMPT = ("在 out 目录建一个 titles.md，写三版不超过二十字的标题，素材："
          "1) 深夜写代码的专注；2) 通勤路上的播客；3) 周末收拾房间。")
WORKER_ID = "dsh"
BASELINE_ID = "claude"


def takeover_worker() -> dict:
    """花名册里的 dsh 条目，只把 adapter 换成 durable，其余（executable / args /
    env / 初审命令 / 预算）原样保留——要接管的是同一个执行体，不是换一个。

    `adapter` 必须在这里就改掉：`bind_durable` 只对**已经是** durable 的条目注入
    端点与令牌（或在 `switch_agents` 里点名的条目）。忘了改，这一单会静默走
    `cli_subproc`，sidecar 里什么都不会有，而遥测和台账看起来全是正常的——
    一个"接管通过"的假阳性。
    """
    roster = load_roster()
    entry = next((a for a in roster.get("agents", []) if a.get("id") == WORKER_ID), None)
    if entry is None:
        raise SystemExit(f"花名册里没有 {WORKER_ID}")
    worker = {key: value for key, value in entry.items() if key != "id"}
    worker["adapter"] = "durable"
    # durable 的 write_file 只认一个根目录；与 cli_subproc 的 AQI_WORKSPACE_ROOT
    # 同源，否则这一单不允许写文件，DSH 交不出真实成果。
    root = str((entry.get("env") or {}).get("AQI_WORKSPACE_ROOT") or "")
    if root:
        worker["workspace_root"] = root
    return worker


def baseline_env(entry: dict) -> dict:
    return {str(k): str(v) for k, v in (entry.get("env") or {}).items()}


def _lookup(supervisor: DurableSupervisor):
    """同步查询内核里某一单的状态；与 app.py 的 _durable_lookup 同源。"""
    import httpx

    endpoint = supervisor.endpoint
    token = supervisor.token

    def lookup(commission_id: str, attempt: int = 0):
        headers = {"authorization": f"Bearer {token}"}
        try:
            with httpx.Client(timeout=10.0) as client:
                found = client.get(f"{endpoint}/v1/lookup", headers=headers,
                                   params={"commission_id": commission_id,
                                           "attempt": int(attempt)}).json()
                if not found.get("found"):
                    return None
                task_id = int(found.get("task_id") or 0)
                if task_id <= 0:
                    return None
                return client.get(f"{endpoint}/v1/runs/{task_id}", headers=headers).json()
        except Exception:
            return None

    return lookup


def _shim_runs(root: Path) -> int:
    """数一数 DSH 一共被拉起过几次：按 durable 工作根下的成果文件计。

    workspace_root 是 `data/commissions`（花名册里 dsh 的 AQI_WORKSPACE_ROOT），
    不在探针的临时目录里。shim 只在真正启动并跑到交差时才写 out/titles.md，
    所以这个数就是"执行体真的跑了几次"。
    """
    total = 0
    for path in Path(root).rglob("titles.md"):
        total += 1
    return total


async def build_service(directory: Path, supervisor: DurableSupervisor,
                        gateway: str = "") -> ExecutiveService:
    roster = load_roster()
    worker = dict(takeover_worker(), id="durable-dsh", name="durable 接管 DSH")
    # 网关覆盖必须写进花名册：sidecar 的子进程拿到的就是这一份 env。只在本地
    # 变量里改，DSH 还是会连花名册里那个 15722（它的上游 15721 没起）。
    if gateway:
        worker["env"] = {**dict(worker.get("env") or {}), "DEEPSEEK_BASE_URL": gateway}
    roster["agents"] = [worker]
    service = ExecutiveService(directory / "state", roster=roster)
    service.bind_durable(supervisor.config(),
                         endpoint_provider=supervisor.current_endpoint)
    return service


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--gateway", default="", help="覆盖 DEEPSEEK_BASE_URL")
    args = parser.parse_args()

    worker = takeover_worker()
    env = baseline_env(worker)
    if args.gateway:
        env["DEEPSEEK_BASE_URL"] = args.gateway
    if args.dry_run:
        print("接管命令：" + " ".join([str(worker.get("executable"))] +
                                     [str(a) for a in (worker.get("args") or [])]))
        print("env：" + ", ".join(sorted(env)))
        print(f"adapter={worker.get('adapter')}  workspace_root={worker.get('workspace_root')}")
        print("DRY-RUN：不真跑。")
        return 0
    if not os.environ.get("LLM_API_KEY"):
        print("SKIP: 没有 LLM_API_KEY")
        return 0

    directory = Path(tempfile.mkdtemp(prefix="takeover-prod-"))
    supervisor = DurableSupervisor(directory=directory / "sidecar", startup_timeout=40.0)
    await supervisor.start()
    verdicts: list[bool] = []
    try:
        import httpx
        headers = {"authorization": f"Bearer {supervisor.token}"}

        # ---------- ① 生产链路：派单 → 接管 → 验收 ----------
        print("[① 生产链路：delegate 派单，durable 接管 DSH]")
        service = await build_service(directory, supervisor, args.gateway)
        outcome = await service.delegate(PROMPT)
        item = service.commission(outcome["commission_id"])
        usage = service.commission_usage(outcome["commission_id"])
        events = service.commission_events(outcome["commission_id"], 200)
        print(f"  status={item['status']}  retry={item.get('retry_count')}")
        print(f"  delivery={(item.get('delivery') or {}).get('summary')}")
        print(f"  question={str(item.get('blocked_question'))[:200]}")
        findings = [str(f.get('rule_id')) for f in ((item.get('audit') or {}).get('findings') or [])]
        print(f"  findings={findings}")
        print(f"  usage={usage}")
        print(f"  台账事件 {len(events)} 条")
        for event in events[-8:]:
            payload = event.get('payload') or {}
            print(f"    {str(event.get('kind')):10s} {str(payload.get('text') or payload)[:140]}")
        ok_status = item["status"] == "ready_to_report"
        ok_usage = int(usage.get("calls") or 0) >= 1
        ok_steps = int(item.get("step_idx") or 0) >= 1
        verdicts += [ok_status, ok_usage, ok_steps]
        print(f"  {'ok' if ok_status else 'FAIL'} 走到待验收")
        print(f"  {'ok' if ok_usage else 'FAIL'} 遥测记到了 DSH 的模型调用")
        print(f"  {'ok' if ok_steps else 'FAIL'} 台账记到了进度步数")

        # 持续监督：把 sidecar 里的事件流打出来，证明是逐步收的，不是一把梭。
        # lookup 必须带 attempt：幂等键是 commission_id@attempt，而初审打回会让
        # attempt 递增（retry_count）。只按 commission_id 查会把"重做过三回的
        # 那一单"误判成"内核里没有这一单"。
        retry = int(item.get("retry_count") or 0)
        listed = httpx.get(f"{supervisor.endpoint}/v1/lookup", headers=headers,
                           params={"commission_id": outcome["commission_id"],
                                   "attempt": retry}, timeout=10.0).json()
        print(f"  lookup={listed}  commission_id={outcome['commission_id']}  attempt={retry}")
        if listed.get("found"):
            view = httpx.get(f"{supervisor.endpoint}/v1/runs/{listed['task_id']}",
                             headers=headers, timeout=10.0).json()
            kinds = [str(e.get("type")) for e in (view.get("events") or [])]
            print(f"  sidecar 事件 {len(kinds)} 条：progress={kinds.count('progress')} "
                  f"token={kinds.count('token')} artifact={kinds.count('artifact')}")
            ok_stream = kinds.count("progress") >= 2
        else:
            print("  FAIL 内核里查不到刚派出去的单")
            ok_stream = False
        verdicts.append(ok_stream)
        print(f"  {'ok' if ok_stream else 'FAIL'} 持续监督：进度是逐步收到的")

        # ---------- ② Python 重启，sidecar 活着且已把活干完 ----------
        # 对账管的是这一种死法：宿主进程重启时 sidecar 没死，它自己把任务跑完了。
        # 此时台账停在 running，若一律转 BLOCKED，DSH 已经写下的成果就被扔掉。
        print("[② 重启对账：Python 重启，内核已把活干完]")
        # 只有内核把活干完，对账才有东西可接。delegate 自己会驱动到终态，
        # 所以先看它停在哪：停在 running 说明内核那边还没终态（或任务还在跑），
        # 此时不该断言"成果被采纳"——那是另一种情形，交给 ③。
        outcome = await service.delegate(PROMPT)
        commission_id = outcome["commission_id"]
        retry2 = int(service.commission(commission_id).get("retry_count") or 0)
        listed = httpx.get(f"{supervisor.endpoint}/v1/lookup", headers=headers,
                           params={"commission_id": commission_id,
                                   "attempt": retry2}, timeout=10.0).json()
        print(f"  lookup={listed}  attempt={retry2}  endpoint={supervisor.endpoint}")
        if not listed.get("found"):
            print("  FAIL 内核里查不到这一单")
            verdicts.append(False)
            service.close()
            return finish(verdicts)
        view = httpx.get(f"{supervisor.endpoint}/v1/runs/{listed['task_id']}",
                         headers=headers, timeout=10.0).json()
        kernel_done = str(view.get("status")) == "terminal" and str(
            (view.get("outcome") or {}).get("status")) == "completed"
        print(f"  内核里这一单：status={view.get('status')}  "
              f"outcome={str((view.get('outcome') or {}).get('status'))}")
        print(f"  台账里这一单：{service.commission(commission_id)['status']}")
        if not kernel_done:
            # 内核侧失败或未终态：对账的正确行为是不采纳。但这也意味着 ② 的
            # 断言没做成——记 False 并让 finish 判 NOT VERIFIED，不能因为
            # "跳过了一段"就打印 VERIFIED。
            print("  FAIL 内核没把活干完，对账无成果可接（③ 仍需单独验证）")
            verdicts.append(False)
            service.close()
            return finish(verdicts)
        service.close()
        # 同一个状态目录、同一个内核：这就是"Python 重启后"的样子。
        # 不复用①那个 service：close() 之后的 CommissionStore 不可再用，重启
        # 对账本来就该在一个全新的 ExecutiveService 上跑。
        service_after = await build_service(directory, supervisor, args.gateway)
        recovered = service_after.recover_interrupted(durable_lookup=_lookup(supervisor))
        adopted = service_after.commission(commission_id)
        print(f"  收口={recovered}  这一单现在是 {adopted['status']}")
        print(f"  交付={(adopted.get('delivery') or {}).get('summary')}")
        ok_adopted = adopted["status"] == "ready_to_report"
        verdicts.append(ok_adopted)
        print(f"  {'ok' if ok_adopted else 'FAIL'} 内核已完成的成果被采纳，没有被判重跑")
        service_after.close()

        # ---------- ③ sidecar 被硬杀：如实转 BLOCKED，重答后能真的重跑 ----------
        # 这一种死法对账管不了：适配器连不上内核，派单只能转 BLOCKED 请示。
        # 要验的是两件事——它没有假装跑完，且老板答一句之后**重新拉起了 DSH**
        # （幂等键按 attempt 分，否则会拿回上一版已终态的任务、原地假装成功）。
        print("[③ sidecar 被硬杀：如实请示，答完真的重跑]")
        supervisor.auto_restart = True
        if supervisor._monitor is None:
            supervisor._monitor = asyncio.create_task(supervisor._watch())
        service2 = await build_service(directory, supervisor, args.gateway)
        commission2 = service2.open("接管实测：硬杀恢复", PROMPT)
        service2.dispatch(commission2["id"], intent="整理标题")
        service2.start(commission2["id"])
        drive = asyncio.create_task(service2.run_commission(commission2["id"]))
        await asyncio.sleep(6.0)
        process = supervisor._process
        killed = process is not None and process.returncode is None
        if killed:
            import signal
            if os.name == "nt":
                import subprocess
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(process.pid)],
                               capture_output=True, timeout=15)
            else:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
        print(f"  杀了正在干活的 sidecar：{killed}")
        # 等它重起：_spawn 自己带 startup_timeout（这里 40s），探针只多给一点
        # 余量。等太短会把"还在起"误报成"起不来"。
        deadline = time.monotonic() + supervisor.startup_timeout + 30.0
        while time.monotonic() < deadline:
            if await supervisor.healthy():
                break
            await asyncio.sleep(0.5)
        revived = await supervisor.healthy()
        print(f"  监督器把内核拉回来了：{revived}")
        try:
            await asyncio.wait_for(drive, timeout=30.0)
        except Exception:
            pass
        item2 = service2.commission(commission2["id"])
        print(f"  硬杀后这一单：{item2['status']}  {str(item2.get('blocked_question'))[:100]}")
        verdicts.append(revived)
        ok_blocked = item2["status"] in ("ready_to_report", "blocked")
        verdicts.append(ok_blocked)
        print(f"  {'ok' if ok_blocked else 'FAIL'} 没有卡死在 running，也没有假装跑完")
        # 答一句，让它重跑。重跑必须真的再拉起一次 DSH：attempt 递增才会拿到
        # 新任务，否则内核里那个已 failed 的任务会被原样拿回，执行体一次都不跑。
        workspace_root = Path(str(takeover_worker().get("workspace_root") or "data/commissions"))
        runs_before = _shim_runs(workspace_root)
        needs_rerun = item2["status"] == "blocked"
        if needs_rerun:
            await service2.answer(commission2["id"], "再试一次")
            item2 = service2.commission(commission2["id"])
            print(f"  答完再试：{item2['status']}  {str(item2.get('blocked_question'))[:160]}")
            print(f"  retry_count={item2.get('retry_count')}")
        runs_after = _shim_runs(workspace_root)
        reran = runs_after > runs_before
        print(f"  成果文件 {runs_before} -> {runs_after}")
        # 只有"确实被打回"才要求重跑；若 DSH 在硬杀前已经交差，这一条不适用。
        # 但重跑必须真的再拉起一次执行体——只看 status=ready_to_report 会把
        # "拿回旧终态任务原样再交"也判成通过，那正是幂等键要防的事。
        ok_rerun = item2["status"] == "ready_to_report" and (reran or not needs_rerun)
        verdicts.append(ok_rerun)
        print(f"  {'ok' if ok_rerun else 'FAIL'} 重做之后走到待验收"
              + (f"（执行体重新拉起：{runs_before}→{runs_after}）" if needs_rerun else ""))
        service2.close()

        # ---------- ④ 兜底：内核不可用时派单明说 ----------
        print("[④ 内核不可用：派单转 BLOCKED 并说明，不假装执行]")
        # 用一个"指向不存在端点"的监督器，而不是把正在用的那个 stop 掉：
        # stop() 之后 _spawn 会抛 AdapterError，模拟的是另一种故障，不是
        # "内核没起来"。这里要验的正是后者。
        dead = DurableSupervisor(directory=directory / "dead-sidecar", startup_timeout=2.0)
        dead.endpoint = "http://127.0.0.1:9"
        dead.token = supervisor.token
        dead.available = True
        service3 = await build_service(directory, dead, args.gateway)
        outcome3 = await service3.delegate(PROMPT)
        item3 = service3.commission(outcome3["commission_id"])
        question = item3.get("blocked_question") or ""
        print(f"  status={item3['status']}  question={question[:120]}")
        ok_unavailable = item3["status"] == "blocked"
        verdicts.append(ok_unavailable)
        print(f"  {'ok' if ok_unavailable else 'FAIL'} 不可用时转 BLOCKED")
        service3.close()
    finally:
        await supervisor.stop()

    return finish(verdicts)


# 四段共 9 条断言：① 状态/遥测/步数 + 持续监督 + ② 对账采纳 + ③ 重起/未假装/重跑
# + ④ 不可用。数量也要对上：早退少跑一段时，不能因为"已跑的都过了"就打印 VERIFIED。
EXPECTED_CHECKS = 9


def finish(verdicts: list[bool]) -> int:
    complete = len(verdicts) >= EXPECTED_CHECKS
    print()
    print("VERIFIED" if verdicts and all(verdicts) and complete else "NOT VERIFIED")
    print(f"明细：{sum(1 for v in verdicts if v)}/{len(verdicts)}（应到 {EXPECTED_CHECKS}）")
    return 0 if verdicts and all(verdicts) and complete else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
