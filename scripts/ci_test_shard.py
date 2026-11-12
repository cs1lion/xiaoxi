"""CI 等价 Python 测试的并发运行器：覆盖不降，只把串行改并发。

2026-11-06：.github/workflows/ci.yaml 原先是
`for f in scripts/test_*.py; do python "$f"; done`，172 个脚本串行实测
13-16 分钟。不能换成 `run_gates.py --always`——那一档只有 72 条，会砍掉
100 个回归；也不能按「慢脚本跳过」提速，那同样是覆盖下降。所以保持全集，
只把「串行」改成「分片并发」。

独占项：test_gate_tmp_leak.py 是 tmp/ 目录**净增**棘轮（它跑 10 个门禁并
比较前后文件数）。2026-11-06 四路并发首跑实测，其他分片同时往 tmp/ 写沙盘，
被它误判成 drill_ws_reconnect 泄漏 +1。判据本身是对的，不该削弱；改成
「并发段全部结束后单独跑」，既保留棘轮又消除观测干扰。

`--self-test` 只验证分片映射（分片并集 + 独占项 = 全集、无重复），不执行
任何脚本；CI 先跑它再实跑，映射漏一个都会当场红。

2026-11-12：把 `agent-runtime/test-*.ts` 也纳入分片。

**为什么**：健康检查实测，80 条 ALWAYS_REQUIRED 门禁里 **17 条是
`agent-runtime/*.ts` 的 Node 门禁**（durable_takeover / durable_recovery /
durable_mcp 等），而 CI 的分片器只 glob `scripts/test_*.py` —— 那 17 条
**从没有任何 CI 步骤执行过**。CI 装了 `agent-runtime` 的 npm 依赖，却一次
都没跑过它的测试。

**为什么用独立的 TS 池而不是混进 Python 池**：Node 门禁的 cwd 是
`agent-runtime/`、命令是 `node --experimental-strip-types test-x.ts`，
与 Python 脚本的形态不同；混在一个池里会让自检的「并集 = 全集」判据失真。
分开成两个池，各自自检，再合并断言。

实测（2026-11-12 本机）：26 个 TS 测试全绿，总耗时 31 秒。用的是
`node:os` 的 `tmpdir()` + `mkdtempSync`，无 Windows 专属假设，Linux CI 可跑。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
AGENT_RUNTIME = ROOT / "agent-runtime"

# 观测全局目录的元门禁，必须等所有并发脚本结束才能跑。
EXCLUSIVE = frozenset({"test_gate_tmp_leak.py"})


def discover() -> list[Path]:
    return sorted(SCRIPTS.glob("test_*.py"))


def discover_ts() -> list[Path]:
    """agent-runtime 的 Node 门禁（2026-11-12 纳入 CI 覆盖）。"""
    return sorted(AGENT_RUNTIME.glob("test-*.ts"))


def assign(scripts: list[Path], workers: int) -> tuple[list[list[Path]], list[Path]]:
    """Round-robin 分片；每片内部仍串行，片与片之间并发。"""
    sharded = [p for p in scripts if p.name not in EXCLUSIVE]
    exclusive = [p for p in scripts if p.name in EXCLUSIVE]
    buckets: list[list[Path]] = [[] for _ in range(workers)]
    for index, path in enumerate(sharded):
        buckets[index % workers].append(path)
    return buckets, exclusive


def run_one(path: Path) -> tuple[str, int, str]:
    # 2026-11-12：编码要**两端都钉死**。
    #
    # 修前是 `capture_output=True, text=True` —— text=True 默认按 locale 编码解码
    # （Windows 本机是 cp936/GBK）。子进程写出 GBK 不可解码的字节（UTF-8 中文、
    # emoji、二进制片段）时，Python 的 _readerthread 抛 UnicodeDecodeError，
    # proc.stdout 变 None，于是下面的 tail 变空串：**失败原因一个字都贴不出来**，
    # CI 日志只剩一个脚本名。stderr 也会同样丢。
    #
    # 只给父进程加 encoding='utf-8' 还不够：实测本机子进程的 stdout 走 locale，
    # 字节是 d6d0cec4...（「中文测试」的 GBK），父进程按 utf-8 解出来是乱码 ——
    # 不丢输出但没法读。所以子进程那边也要用 PYTHONIOENCODING=utf-8 钉成写 utf-8。
    #
    # scripts/run_gates.py:1322 早就写对了父进程这一半（utf-8 + replace），
    # 但同样漏了子进程那一半 —— 它的失败输出在本机也是乱码。
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=900,
                              env=env)
    except subprocess.TimeoutExpired:
        return path.name, 124, f"timeout after 900s: {path.name}"
    if proc.returncode == 0:
        return path.name, 0, ""
    tail = ((proc.stdout or "")[-1200:] + (proc.stderr or "")[-1200:])
    return path.name, proc.returncode, tail


def run_one_ts(path: Path) -> tuple[str, int, str]:
    """跑一个 agent-runtime 的 Node 门禁。

    cwd 必须是 agent-runtime/：那些测试用相对路径读自己的 fixture 与
    `import.meta.dirname` 定位 echo server 脚本。命令形态与 run_gates.py 里
    的 durable_* 门禁一致（`node --experimental-strip-types test-x.ts`）。
    """
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    try:
        proc = subprocess.run(
            ["node", "--experimental-strip-types", path.name],
            cwd=str(AGENT_RUNTIME), capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=900, env=env)
    except subprocess.TimeoutExpired:
        return path.name, 124, f"timeout after 900s: {path.name}"
    except FileNotFoundError as exc:
        return path.name, 127, f"node not found: {exc}"
    if proc.returncode == 0:
        return path.name, 0, ""
    tail = ((proc.stdout or "")[-1200:] + (proc.stderr or "")[-1200:])
    return path.name, proc.returncode, tail


def run_bucket(bucket: list[Path]) -> list[tuple[str, int, str]]:
    return [run_one(path) for path in bucket]


def run_bucket_ts(bucket: list[Path]) -> list[tuple[str, int, str]]:
    return [run_one_ts(path) for path in bucket]


def self_test(workers: int) -> int:
    scripts = discover()
    if not scripts:
        print("FAIL: scripts/test_*.py 一个都没发现")
        return 1
    if len({p.name for p in scripts}) != len(scripts):
        print("FAIL: discover() 出现重复文件名")
        return 1
    unknown = EXCLUSIVE - {p.name for p in scripts}
    if unknown:
        print(f"FAIL: 独占项在磁盘上不存在：{sorted(unknown)}")
        return 1
    buckets, exclusive = assign(scripts, workers)
    flat = [p for bucket in buckets for p in bucket] + exclusive
    if len(flat) != len(scripts):
        print(f"FAIL: 分片+独占总数 {len(flat)} != 全集 {len(scripts)}")
        return 1
    if sorted(p.name for p in flat) != sorted(p.name for p in scripts):
        print("FAIL: 分片并集与全集不一致（漏跑或重复）")
        return 1
    sizes = [len(b) for b in buckets]
    if max(sizes) - min(sizes) > 1:
        print(f"FAIL: 分片不均 {sizes}")
        return 1
    print(f"OK: {len(scripts)} 个脚本 = 并发 {sum(sizes)} + 独占 {len(exclusive)}；"
          f"{workers} 片每片 {sizes}，并集=全集、无重复")

    # 2026-11-12：TS 池自检。Node 门禁独立成池，映射判据与 Python 池同款。
    ts_scripts = discover_ts()
    if not ts_scripts:
        print("FAIL: agent-runtime/test-*.ts 一个都没发现")
        return 1
    if len({p.name for p in ts_scripts}) != len(ts_scripts):
        print("FAIL: discover_ts() 出现重复文件名")
        return 1
    ts_buckets: list[list[Path]] = [[] for _ in range(workers)]
    for index, path in enumerate(ts_scripts):
        ts_buckets[index % workers].append(path)
    ts_flat = [p for bucket in ts_buckets for p in bucket]
    if len(ts_flat) != len(ts_scripts):
        print(f"FAIL: TS 分片总数 {len(ts_flat)} != 全集 {len(ts_scripts)}")
        return 1
    if sorted(p.name for p in ts_flat) != sorted(p.name for p in ts_scripts):
        print("FAIL: TS 分片并集与全集不一致（漏跑或重复）")
        return 1
    ts_sizes = [len(b) for b in ts_buckets]
    if max(ts_sizes) - min(ts_sizes) > 1:
        print(f"FAIL: TS 分片不均 {ts_sizes}")
        return 1
    print(f"OK: {len(ts_scripts)} 个 Node 门禁 = 并发 {sum(ts_sizes)}；"
          f"{workers} 片每片 {ts_sizes}，并集=全集、无重复")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="并发跑 CI 等价测试全集（Python + agent-runtime Node）")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--self-test", action="store_true")
    parser.add_argument("--skip-ts", action="store_true",
                        help="只跑 Python 池（本地缺 node 或 agent-runtime 依赖时用；CI 不该带这个）")
    args = parser.parse_args()
    if args.workers < 1:
        print("FAIL: --workers 必须 >= 1")
        return 2
    if args.self_test:
        return self_test(args.workers)

    scripts = discover()
    buckets, exclusive = assign(scripts, args.workers)
    print(f"[shard] {len(scripts)} scripts / {args.workers} workers / "
          f"exclusive={[p.name for p in exclusive]}", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        per_bucket = list(pool.map(run_bucket, buckets))
    results = [item for bucket in per_bucket for item in bucket]
    # 观测全局 tmp/ 的元门禁：并发段全部结束后才跑，避免别人的沙盘被算成泄漏。
    for path in exclusive:
        print(f"[exclusive] {path.name}", flush=True)
        results.append(run_one(path))
    print(f"[shard] ran {len(results)} / {len(scripts)}", flush=True)
    if len(results) != len(scripts):
        print("FAIL: 实际执行数不等于全集")
        return 1
    failed = [item for item in results if item[1] != 0]
    for name, code, tail in failed:
        print(f"FAIL: {name} (exit {code})\n{tail}\n", flush=True)
    if failed:
        print(f"::error::以下测试脚本失败：{' '.join(name for name, _, _ in failed)}")
        return 1
    print("OK: 全部脚本通过")

    # 2026-11-12：agent-runtime 的 Node 门禁池。这批门禁此前**从没有任何 CI 步骤
    # 执行过**（CI 装了 agent-runtime 的 npm 依赖，却一次都没跑它的测试）。
    if args.skip_ts:
        print("[shard] --skip-ts：跳过 Node 门禁池")
        return 0
    ts_scripts = discover_ts()
    if not ts_scripts:
        print("FAIL: agent-runtime/test-*.ts 一个都没发现")
        return 1
    ts_buckets: list[list[Path]] = [[] for _ in range(args.workers)]
    for index, path in enumerate(ts_scripts):
        ts_buckets[index % args.workers].append(path)
    print(f"[shard] {len(ts_scripts)} node gates / {args.workers} workers", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        ts_per_bucket = list(pool.map(run_bucket_ts, ts_buckets))
    ts_results = [item for bucket in ts_per_bucket for item in bucket]
    print(f"[shard] ran {len(ts_results)} / {len(ts_scripts)} node gates", flush=True)
    if len(ts_results) != len(ts_scripts):
        print("FAIL: Node 门禁实际执行数不等于全集")
        return 1
    ts_failed = [item for item in ts_results if item[1] != 0]
    for name, code, tail in ts_failed:
        print(f"FAIL: {name} (exit {code})\n{tail}\n", flush=True)
    if ts_failed:
        print(f"::error::以下 Node 门禁失败：{' '.join(name for name, _, _ in ts_failed)}")
        return 1
    print("OK: 全部 Node 门禁通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
