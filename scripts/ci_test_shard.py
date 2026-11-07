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
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"

# 观测全局目录的元门禁，必须等所有并发脚本结束才能跑。
EXCLUSIVE = frozenset({"test_gate_tmp_leak.py"})


def discover() -> list[Path]:
    return sorted(SCRIPTS.glob("test_*.py"))


def assign(scripts: list[Path], workers: int) -> tuple[list[list[Path]], list[Path]]:
    """Round-robin 分片；每片内部仍串行，片与片之间并发。"""
    sharded = [p for p in scripts if p.name not in EXCLUSIVE]
    exclusive = [p for p in scripts if p.name in EXCLUSIVE]
    buckets: list[list[Path]] = [[] for _ in range(workers)]
    for index, path in enumerate(sharded):
        buckets[index % workers].append(path)
    return buckets, exclusive


def run_one(path: Path) -> tuple[str, int, str]:
    try:
        proc = subprocess.run([sys.executable, str(path)], cwd=str(ROOT),
                              capture_output=True, text=True, timeout=900)
    except subprocess.TimeoutExpired:
        return path.name, 124, f"timeout after 900s: {path.name}"
    if proc.returncode == 0:
        return path.name, 0, ""
    tail = ((proc.stdout or "")[-1200:] + (proc.stderr or "")[-1200:])
    return path.name, proc.returncode, tail


def run_bucket(bucket: list[Path]) -> list[tuple[str, int, str]]:
    return [run_one(path) for path in bucket]


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
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="并发跑 CI 等价 Python 测试全集")
    parser.add_argument("--workers", type=int, default=4)
    parser.add_argument("--self-test", action="store_true")
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
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
