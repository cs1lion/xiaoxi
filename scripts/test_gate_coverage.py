# -*- coding: utf-8 -*-
"""门禁覆盖度棘轮：scripts/test_*.py 要么登记进 run_gates，要么进未登记清单。

为什么需要它
------------
CI 与本地门禁跑的不是同一个集合。CI 跑 for f in scripts/test_*.py 的全部命中，
run_gates.py 只登记其中一部分。差集里的脚本坏了没人知道。

实测后果：master 连续红了好几次，日志只有一句 Process completed with exit
code 1. —— 因为「哪个脚本红的」这个名字只出现在 ::group:: 里，没人去翻。
那几条红的脚本全部都在差集里（既没登记、也没人认领）。

本门禁不判断脚本跑得对不对（那是脚本自己的事），只管一件事：
新增一个没人登记的测试脚本，当场红。

三种失败
--------
1. 磁盘上有、既没登记也不在清单里 —— 新增未登记脚本（棘轮拦的就是这个）
2. 清单里列着、磁盘上文件已不存在 —— 清单过期
3. 清单里列着、其实已经登记了 —— 重复，该从清单删掉

--write 重新生成清单。改完登记状态后跑一次，不要手抄。它只是记账，
不代表清单里的脚本已被认可 —— 每一条都是待认领的债。

用法：
    python scripts/test_gate_coverage.py            # 断言
    python scripts/test_gate_coverage.py --write    # 重新生成清单
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GATES_FILE = ROOT / 'scripts' / 'run_gates.py'
SCRIPTS_DIR = ROOT / 'scripts'
LIST_FILE = SCRIPTS_DIR / 'gates_unregistered.txt'

# 登记形态有两种：cmd 数组里的 scripts/test_x.py，和裸 test_x.py（node/TS 那类）。
# 用正则而不是 import run_gates：它顶层会算 PY 等环境量，门禁自检不该依赖
# 被检对象本身能被执行。
_REGISTERED = re.compile(r'["\'](?:scripts[/\\\\])?(test_[A-Za-z0-9_]+[.]py)["\']')


def registered_names() -> set[str]:
    src = GATES_FILE.read_text(encoding='utf-8')
    return set(_REGISTERED.findall(src))


def on_disk_names() -> list[str]:
    return sorted(path.name for path in SCRIPTS_DIR.glob('test_*.py'))


def read_list() -> list[str]:
    if not LIST_FILE.exists():
        return []
    text = LIST_FILE.read_text(encoding='utf-8')
    return [line.strip() for line in text.splitlines()
            if line.strip() and not line.strip().startswith('#')]


def write_list(unregistered: list[str], registered: int) -> None:
    header = [
        '# 未登记进 run_gates 的测试脚本清单（棘轮基线，不是许可名单）',
        '#',
        '# 这些脚本会被 CI 的 for f in scripts/test_*.py 跑到，但不在本地',
        '# python scripts/run_gates.py 的门禁里 —— 所以「本地全绿」不等于「CI 绿」。',
        '#',
        '# 认领方式：在 scripts/run_gates.py 的 GATES 里加一条，从本清单删掉，',
        '# 再跑 python scripts/test_gate_coverage.py --write 对齐。',
        '# 新增 scripts/test_*.py 而不登记、也不进本清单 —— 门禁直接红。',
        '#',
        '# 由 test_gate_coverage.py --write 生成；条数与登记数以',
        '# python scripts/test_gate_coverage.py 的输出为准，不写在这里。',
        '',
    ]
    # 并集式写入：只增不减。清单按「两边 master 的并集」维护 —— 某个脚本
    # 可能在另一个分支上还没出现（判 warn 不判失败，见主流程），--write 若
    # 纯按本机磁盘重写就会把这些条目悄悄丢掉，换一个分支跑就红。
    # 要减条目得手工删（配合 --write 会保留），删对了才有「已登记却仍留在
    # 清单里」那条失败来兜底。
    kept = set(read_list())
    merged = sorted(kept | set(unregistered))
    LIST_FILE.write_text('\n'.join(header + merged) + '\n', encoding='utf-8')


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description='门禁覆盖度棘轮')
    parser.add_argument('--write', action='store_true', help='重新生成未登记清单')
    args = parser.parse_args(argv)

    disk = on_disk_names()
    disk_set = set(disk)
    registered = registered_names()
    unregistered = disk_set - registered

    if args.write:
        write_list(sorted(unregistered), len(registered & disk_set))
        print('已写入 %s：未登记 %d 条，已登记 %d 条'
              % (LIST_FILE.name, len(unregistered), len(registered & disk_set)))
        return 0

    listed = read_list()
    listed_set = set(listed)
    problems: list[str] = []

    for name in sorted(unregistered - listed_set):
        problems.append('未登记也不在清单里：scripts/%s'
                        '（要么进 run_gates.GATES，要么 --write 收进清单）' % name)
    # 清单里有、磁盘上没有：只告警。清单允许比当前分支更宽 ——
    # 它是按「两边 master 的并集」生成的（见清单头部），所以旧基线上
    # 会看到几条尚未出现的脚本。判失败会让同一个 PR 在推送前后各红一次。
    stale = sorted(listed_set - disk_set)
    for name in sorted(listed_set & registered):
        problems.append('已登记却仍留在清单里（该删）：scripts/%s' % name)

    print('磁盘 test_*.py：%d   已登记：%d   清单：%d   未登记：%d'
          % (len(disk_set), len(registered & disk_set),
             len(listed_set & disk_set), len(unregistered)))
    if problems:
        for problem in problems:
            print('  FAIL ' + problem)
        print('')
        print('FAILED %d' % len(problems))
        return 1
    for name in stale:
        print('  warn 清单里列着但本分支还没有：scripts/%s' % name)
    print('')
    print('PASS 覆盖度棘轮成立（登记 %d + 清单 %d = 磁盘 %d）'
          % (len(registered & disk_set), len(listed_set & disk_set), len(disk_set)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
