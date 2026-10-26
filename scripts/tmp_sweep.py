#!/usr/bin/env python
"""tmp_sweep：tmp/ 只增不减的兜底清扫与计数棘轮。

为什么需要它：evals/checks.py 的 fastloop 沙盘曾每次留一个
tmp/eval-fastloop-* 目录（源头已在 checks.py 修掉），但 tmp/ 里还有
历史残留与其它生产者。tmp/ 在 .gitignore 里，git status 永远看不见它，
「工作区干净」从来不是 tmp 卫生的证据。

设计约束：
- 只扫 tmp/（.gitignore:59），删不到仓库文件，也不碰 data/ 与 .venv；
- 按 mtime 超过 --ttl-days（默认 7）才删，宽限期内一律保留；
- 默认 dry-run 只报数，--yes 才真删（对齐 archive_durable.py 的 --yes）；
- tmp/KEEP 标记文件存在则整体不扫；
- 不跟随 symlink；
- --write 生成计数上限文件（带日期与 HEAD 锚，遵守 AGENTS.md 三分法），
  --ceiling 模式下计数超上限即 exit 1（门禁棘轮用）。

用法：
    python scripts/tmp_sweep.py                    # dry-run，报将删/保留
    python scripts/tmp_sweep.py --yes              # 真删超期条目
    python scripts/tmp_sweep.py --write scripts/tmp_ceiling.txt   # 生成上限
    python scripts/tmp_sweep.py --ceiling scripts/tmp_ceiling.txt # 棘轮模式
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ROOT = ROOT / "tmp"
KEEP_MARKER = "KEEP"
PREFIX = "[tmp_sweep]"


def _head_anchor() -> str:
    """Current commit short SHA for the ceiling file's time anchor."""
    try:
        out = subprocess.run(["git", "rev-parse", "--short", "HEAD"],
                             capture_output=True, text=True, cwd=ROOT, timeout=10)
        return out.stdout.strip() or "unknown"
    except Exception:
        return "unknown"


def scan(root: Path, ttl_days: float) -> tuple[list[Path], list[Path]]:
    """Return (expired, kept) direct children of root.

    A child is expired when its mtime is older than the TTL. Symlinks are
    never followed and never deleted. The KEEP marker short-circuits the
    whole scan.
    """
    if not root.is_dir():
        return [], []
    if (root / KEEP_MARKER).exists():
        return [], []
    cutoff = time.time() - max(0.0, ttl_days) * 86400.0
    expired: list[Path] = []
    kept: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_symlink():
            kept.append(child)          # 符号链接不碰
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            kept.append(child)
            continue
        if mtime < cutoff:
            expired.append(child)
        else:
            kept.append(child)
    return expired, kept


def _fmt(paths: list[Path], root: Path) -> list[str]:
    return [str(p.relative_to(root)) for p in paths]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="tmp/ 兜底清扫与计数棘轮")
    parser.add_argument("--root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--ttl-days", type=float, default=7.0)
    parser.add_argument("--yes", action="store_true", help="真删；缺省只 dry-run")
    parser.add_argument("--dry-run", action="store_true",
                        help="显式 dry-run（本就是缺省行为，与 --yes 对偶）")
    parser.add_argument("--headroom", type=int, default=100,
                        help="--write 时在当期总数上加的余量，吸收并发 session 的正常波动")
    parser.add_argument("--write", type=Path, default=None,
                        help="把当前条目总数写入上限文件（带日期与 HEAD 锚）")
    parser.add_argument("--ceiling", type=Path, default=None,
                        help="棘轮模式：总数超上限即 exit 1")
    args = parser.parse_args(argv)

    # 安全承诺是强制的，不是缺省巧合：--root 必须解析在仓库 tmp/ 内。
    # 删除工具不得被指向 data/ 或 .venv（项目红线：删数据须先问）。
    root = args.root.resolve()
    fence = DEFAULT_ROOT.resolve()
    if root != fence and fence not in root.parents:
        print(f"{PREFIX} 拒绝：--root 必须在 {fence} 内（当前 {root}）。"
              f"本工具只清 tmp/，不碰 data/ 与 .venv。")
        return 1
    args.root = root

    if not args.root.is_dir():
        print(f"{PREFIX} 根目录不存在：{args.root}")
        return 1

    expired, kept = scan(args.root, args.ttl_days)
    total = len(expired) + len(kept)
    print(f"{PREFIX} 根 {args.root}，TTL {args.ttl_days:g} 天，共 {total} 项"
          f"（超期 {len(expired)} / 保留 {len(kept)}）")
    if args.root.joinpath(KEEP_MARKER).exists():
        print(f"{PREFIX} {KEEP_MARKER} 标记存在，整体不扫")

    if args.write is not None:
        stamp = time.strftime("%Y-%m-%d")
        head = max(0, args.headroom)
        ceiling = total + head
        body = [
            "# tmp/ 条目数上限棘轮（不是许可名单，是当期水位 + 余量）",
            f"# 生成于 {stamp}，HEAD {_head_anchor()}，TTL {args.ttl_days:g} 天，",
            f"# 当期 {total} 项 + 余量 {head}。余量吸收并发 session 的正常波动；",
            "# 超限说明 tmp/ 又在只增不减，先查生产者（evals/checks.py 的沙盘",
            "# 应随用随清），确认泄漏后 --yes 清扫再 --write 重置水位。",
            f"ceiling = {ceiling}",
        ]
        args.write.write_text("\n".join(body) + "\n", encoding="utf-8")
        print(f"{PREFIX} 上限已写入 {args.write}：{ceiling}（当期 {total} + 余量 {head}）")

    if args.ceiling is not None:
        try:
            text = args.ceiling.read_text(encoding="utf-8")
            ceiling = int(next(line.split("=", 1)[1].strip()
                               for line in text.splitlines()
                               if line.strip().startswith("ceiling")))
        except Exception as exc:
            print(f"{PREFIX} 上限文件读取失败：{args.ceiling}（{type(exc).__name__}: {exc}）")
            return 1
        if total > ceiling:
            print(f"{PREFIX} 失败：tmp/ 条目 {total} 超上限 {ceiling}，"
                  f"又在只增不减；先查生产者，再按需 --write 抬水位")
            return 1
        print(f"{PREFIX} 通过：{total} <= 上限 {ceiling}")

    if expired:
        preview = "、".join(_fmt(expired[:5], args.root))
        more = " 等" if len(expired) > 5 else ""
        if args.yes:
            removed = 0
            for path in expired:
                if path.is_dir() and not path.is_symlink():
                    shutil.rmtree(path, ignore_errors=True)
                else:
                    try:
                        path.unlink()
                    except OSError:
                        pass
                removed += 1
            print(f"{PREFIX} 已删除 {removed} 项：{preview}{more}")
        else:
            print(f"{PREFIX} 将删 {len(expired)} 项（dry-run，加 --yes 才真删）："
                  f"{preview}{more}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
