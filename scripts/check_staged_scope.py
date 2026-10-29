# -*- coding: utf-8 -*-
"""提交前的暂存范围检查：暂存区里不能有没有你痕迹的文件。

**为什么需要它**：共享仓库里 `git add <我的文件> && git commit` 不等于只提交
我的文件。`git add` 只把指定路径加进索引，而 `git commit` 提交的是**整个
索引**——并发会话在此期间暂存的任何文件都会被一起带走。2026-10-27 一个 8 小时
会话里这个形态重复了至少 8 次，多数靠提交前主动核对 `git diff --cached --stat`
发现，两次差点直接提交（一次 `git add` 后直接 commit，一次 plumbing 漏传
GIT_INDEX_FILE 读到被污染的主索引，把别人的 12 个文件 2970 insertions 一起
提交，靠 update-ref 回退）。

按 AGENTS.md 的 Encode Lessons in Structure：同一条纪律写第二遍就该变成检查，
而不是更多文字。

**为什么必须显式声明（本脚本最有价值的教训）**：第一版想全自动判归属，两种
信号都试了，都不成立——
- 「工作区有未提交改动」：共享工作区里任何人改动都出现在 `git diff` 里，
  分不出是谁的。实测把并发 session 在途的 run_evals.py 判成我的，报干净；
- 「本会话提交碰过的路径」：并发 session 的提交也在 `origin/master..HEAD`
  区间里（他们用同一本地分支），他们的文件同样被算成我的。

即：**共享工作区里没有任何 git 信号能区分「谁改的」**。能判定的只有意图，
而意图只能由提交者声明。所以本脚本收窄为一句可执行的话：

    暂存区 ⊆ 我声明要提交的路径

多出来的就是污染，附撤出命令。这让「提交前核对范围」从一句纪律变成一个
退出码，代价是每次提交多打一行路径——比起第 9 次卷入别人的文件，这个代价
可以忽略。

已知边界（不冒充完备）：声明之后仍要逐 hunk 看 `git diff --cached`——别人若
改了**同一个文件**，文件名级检查看不出那份改动是不是你的。本脚本只抓
「整份文件都不该在这」这一形态，它恰好是那 8 次事故的共同形态。

用法：
    python scripts/check_staged_scope.py <路径> [<路径> ...]
    python scripts/check_staged_scope.py --stdin   < 路径清单

Exit: 0 = 暂存区为空，或暂存区全部落在声明范围内；
      1 = 有声明外文件（附撤出命令）；
      2 = 参数或 git 环境异常。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def git(*args: str, root: Path | None = None) -> str:
    cwd = root if root is not None else ROOT
    r = subprocess.run(["git", *args], cwd=str(cwd), capture_output=True)
    if r.returncode != 0:
        sys.stderr.write(f"[check_staged_scope] git {' '.join(args)} 失败："
                         f"{r.stderr.decode('utf-8', 'replace').strip()}\n")
        raise SystemExit(2)
    return r.stdout.decode("utf-8", "replace")


def staged_paths(root: Path | None = None) -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", root=root)
    return [p for p in out.split("\n") if p.strip()]


def read_declared(argv: list[str]) -> list[str]:
    if "--stdin" in argv:
        rest = [a for a in argv if a != "--stdin"]
        if rest:
            sys.stderr.write("[check_staged_scope] --stdin 不接受其他参数\n")
            raise SystemExit(2)
        return [p.strip() for p in sys.stdin.read().split("\n") if p.strip()]
    if not argv:
        sys.stderr.write(__doc__)
        raise SystemExit(2)
    return [a for a in argv if a.strip()]


def main(argv: list[str], root: Path | None = None) -> int:
    """跑一次检查。`root` 仅供测试注入临时仓库，生产调用不传（用 ROOT）。"""
    declared = set(read_declared(argv))
    staged = staged_paths(root)

    if not staged:
        print("[check_staged_scope] 暂存区为空，无可提交内容。")
        return 0

    foreign = [p for p in staged if p not in declared]

    if not foreign:
        print(f"[check_staged_scope] 干净：暂存区 {len(staged)} 个文件全部在声明范围内。")
        return 0

    print(f"[check_staged_scope] 暂存区 {len(staged)} 个文件，其中 {len(foreign)} 个"
          f"不在你声明的范围内：")
    for p in foreign:
        print(f"    {p}")
    print("\n撤出命令：")
    print("    git reset HEAD -- " + " ".join(foreign))
    print("\n若其中确有你要提交的，把它加进声明参数重跑。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
