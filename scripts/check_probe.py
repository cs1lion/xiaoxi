# -*- coding: utf-8 -*-
"""探针有效性自检：证明「探针能红」再相信「探针说绿」。

**为什么需要它**：2026-10-27 一个长会话里，「探针/判据无效」类事故出现了
23 次，是本会话最高频的失败形态。三种典型：

1. **等价改动**（plan-108 连踩两次）：把 `is_absolute()` 从断言里去掉、
   把 `all(...)` 换成 `True`——以为在造破坏版，实际造的是等价改写，破坏版
   照样全绿。当时误判成「helper 没被共用」，差点删掉正确的重构；
2. **量错路径**（plan-14 / plan-28 / plan-45）：探针打点验证时发现量的不是
   目标分支——正则提前截断、用同一条消息当 current 使行数不够、epiphany 边
   为空使一处卸载不可达。「不修」的结论比「修」的结论更容易被它伪证成数据；
3. **锚点撞多处**（plan-108 第三次）：注入脚本的锚点在文件里出现两次，
   `replace` 改错了地方或直接 assert 失败。

共同根因是**验证工具本身没被验证**。一个探针说「绿」，你无法区分「被测
性质成立」和「探针根本没在测」——除非先证明它红过。

**用法**：把一个探针写成「可注入」的形态，然后用本工具跑两步自检。

    from check_probe import probe_self_check

    result = probe_self_check(
        name="worker_shim_contract 被四个 shim 共用",
        run=lambda: subprocess.run([...], capture_output=True).returncode,
        break_it=lambda: <让被测性质不成立的改动>,
        restore=lambda: <还原>,
    )
    if not result.ok:
        raise SystemExit(result.report())

**判据**：`break_it()` 之后必须 exit≠0（探针能红），`restore()` 之后必须
exit==0（还原后能绿）。两步都过才认这个探针有效。

**为什么这个判据能抓住那三种失败**：
- 等价改动 → 破坏版也绿 → 第一步就红，探针无效；
- 量错路径 → 破坏版动的不是被测分支 → 第一步红；
- 锚点撞多处 → `break_it` 里应有 `assert count==1`，撞了会当场炸而不是
  静默改错地方。

**边界**：本工具只证「探针对这个注入敏感」，不证「这个注入覆盖了所有
该覆盖的回归」。后者仍需人判断注入点选得对不对——所以 `break_it` 的lambda
要写在探针旁边，让review的人看见你注入了什么。

Run（自检用，不单独跑）：被其他探针 import。
"""
from __future__ import annotations

import shutil
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable


@dataclass
class ProbeResult:
    """一次探针自检的结果。`ok` 是唯一该看的字段，其余都是解释。"""
    name: str
    ok: bool
    break_exit: int | None = None
    restore_exit: int | None = None
    error: str = ""
    notes: list[str] = field(default_factory=list)

    def report(self) -> str:
        lines = [f"探针自检：{self.name}"]
        lines.append(f"  破坏版 exit={self.break_exit}（必须非 0，证明探针能红）")
        lines.append(f"  还原后 exit={self.restore_exit}（必须为 0，证明还原干净）")
        if self.error:
            lines.append(f"  注入/还原过程抛错：{self.error}")
        for note in self.notes:
            lines.append(f"  备注：{note}")
        lines.append(f"  结论：{'探针有效' if self.ok else '探针无效——上面的结论不可信'}")
        return "\n".join(lines)


def probe_self_check(name: str,
                     run: Callable[[], int],
                     break_it: Callable[[], None],
                     restore: Callable[[], None]) -> ProbeResult:
    """跑两步自检：破坏版必须红，还原后必须绿。

    `run` 返回被测目标的退出码（0 = 绿）。`break_it` / `restore` 是注入与
    还原；`restore` 一定会被调用（finally），注入炸了也要还原，否则会把破坏
    留在仓库里——那比探针无效更糟。
    """
    result = ProbeResult(name=name, ok=False)
    try:
        break_it()
    except Exception:
        result.error = "break_it 抛错（锚点撞多处或写法错）：\n" + traceback.format_exc(limit=3)
        try:
            restore()
        except Exception:
            result.error += "\n还原也失败了，仓库可能留在破坏态，先手动检查！"
        return result

    try:
        result.break_exit = run()
    finally:
        try:
            restore()
        except Exception:
            result.error = ("restore 抛错，仓库可能留在破坏态：\n"
                            + traceback.format_exc(limit=3))
            return result

    try:
        result.restore_exit = run()
    except Exception:
        result.error = "还原后复跑抛错：\n" + traceback.format_exc(limit=3)
        return result

    result.ok = (result.break_exit not in (0, None)
                 and result.restore_exit == 0)
    if result.break_exit == 0:
        result.notes.append("破坏版也绿了——这是等价改动或量错路径，不是有效注入")
    if result.restore_exit != 0:
        result.notes.append("还原后仍红——还原不干净，或原版本来就是红的")
    return result


def patch_file(path: Path, old: str, new: str) -> Callable[[], None]:
    """造一个「把 old 换成 new」的注入，返回还原函数。

    自带 `count(old) == 1` 检查——锚点撞多处时当场炸，不会静默改错地方
    （plan-108 第三次失败的形态）。

    **二进制读写，不用 text 模式**：text 模式在 Windows 上会把 CRLF 文件的行尾
    改写成 LF。plan-110 审计四个 CRLF 测试文件后，`git status` 显示 4 个 M，
    内容虽逐字节一致（sha256 相同）但行尾已变，只能靠 `git checkout HEAD --`
    归位。还原必须逐字节复原，否则下次忘了归位就会把格式改动混进提交。

    原内容备份在调用方的临时目录里，由返回的 restore 负责写回。
    """
    raw = path.read_bytes()
    text = raw.decode("utf-8")
    count = text.count(old)
    if count != 1:
        raise AssertionError(
            f"锚点 {old[:60]!r} 在 {path.name} 里出现 {count} 次，期望 1 次。"
            f"撞多处时 replace 会改错地方或漏改——先换更唯一的锚点。")

    backup_dir = Path(tempfile.mkdtemp(prefix="probe-backup-"))
    backup = backup_dir / path.name
    backup.write_bytes(raw)

    def restore() -> None:
        path.write_bytes(backup.read_bytes())
        shutil.rmtree(backup_dir, ignore_errors=True)

    def inject() -> None:
        path.write_bytes(text.replace(old, new).encode("utf-8"))

    inject()
    return restore
