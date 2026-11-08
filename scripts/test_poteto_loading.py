"""poteto-mode 加载契约探针（离线、零 Key、零会话依赖）。

边界（别高估）：本脚本**不能**证明「新会话真的把 SKILL.md 读进上下文」——
那需要会话级 hook / Bot instructions，当前仓库没有，AGENTS.md:138-140 也自承
「不是硬约束」。它验证的是**加载链路的文件契约完整 + 适配层入库状态**：
想按规矩读的时候，路径都在、规矩都还在、两份本机修正没被 .gitignore 吞掉、
且已经进过 HEAD（重装 pstack 后能从 git 检出恢复）。这是从「写下了规矩」到
「可复跑检查」的最小一步，不把它说成「已验证 mode 生效」。

2026-11-06：AGENTS.md 声明「全局默认 poteto-mode（A 档）」，但 53/54 个
pstack SKILL.md 带 disable-model-invocation: true，config/ 与 scripts/ 零绑定，
也没有任何加载断言。本探针补上可自动化的那半边。
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
AGENTS = ROOT / ".agents" / "skills"
passed, failed = 0, []


def check(name: str, condition: bool, detail: str = "") -> None:
    global passed
    if condition:
        passed += 1
        print(f"  ok   {name}")
    else:
        failed.append(name)
        print(f"  FAIL {name}" + (f"  {detail}" if detail else ""))


def resolve(base: Path, link: str) -> Path:
    """真实路径解析：完整保留 ../ 层数，不用会吃掉 ../ 的正则。"""
    return (base.parent / link).resolve()


def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=str(ROOT),
                          capture_output=True, text=True)


def main() -> int:
    entry = AGENTS / "poteto-mode" / "SKILL.md"
    body = AGENTS / "pstack" / "poteto-mode" / "SKILL.md"
    dsh = AGENTS / "pstack" / "DSH.md"

    # .agents/ 被 .gitignore 逐层排除（见 AGENTS.md「已知残缺」），fresh clone /
    # CI checkout 里根本没有 poteto bundle。这时「文件不存在」是环境事实，不是
    # 加载契约坏了，按 SKIP 处理（CI 的分片运行器只认 exit 0，跳过即通过）。
    # 只在两个 bundle 目录都不存在时跳过；目录在、个别文件缺，仍按下面的断言判红，
    # 不把「装坏了」伪装成「没安装」。
    bundle_dirs = (AGENTS / "pstack" / "poteto-mode", AGENTS / "poteto-mode")
    if not any(path.is_dir() for path in bundle_dirs):
        print("SKIP: .agents bundle not present (fresh clone / CI checkout)")
        print("\n=== 0 passed, 0 failed (skipped) ===")
        return 0

    check("visible entry 存在", entry.is_file(), str(entry))
    check("入口指向的正文可解析", body.is_file(), str(body))
    check("DSH.md 本机适配层存在", dsh.is_file(), str(dsh))
    if not (entry.is_file() and body.is_file() and dsh.is_file()):
        return finish()

    entry_text = entry.read_text(encoding="utf-8")
    check("入口的正文链接真实可解析",
          resolve(entry, "../pstack/poteto-mode/SKILL.md") == body.resolve())
    check("入口的 DSH 链接真实可解析",
          resolve(entry, "../pstack/DSH.md") == dsh.resolve())

    body_text = body.read_text(encoding="utf-8")
    for phrase in ("Just do it", "Never Block on the Human", "Always pause"):
        check(f"Autonomy 含 {phrase}", phrase in body_text)

    agents_text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    check("AGENTS.md 声明默认 A 档", "A 档" in agents_text)
    # 诚实口径：上游正文没有 A/B/C 档位体系，B/C 查无定义；别把不存在的
    # 东西当成「三档已实现」写进断言。
    check("上游正文只有 Autonomy 节、无 B/C 档",
          "B 档" not in body_text and "C 档" not in body_text)

    playbooks = sorted((AGENTS / "pstack" / "poteto-mode" / "playbooks").glob("*.md"))
    principles = sorted(AGENTS.glob("pstack/principle-*/SKILL.md"))
    check("playbook 叶子非空", len(playbooks) >= 1, str(len(playbooks)))
    check("principle 叶子非空", len(principles) >= 1, str(len(principles)))

    # 已知残缺不判红，但必须仍被如实记录，防止有人悄悄把「已知残缺」
    # 改口成「已修好」。
    dsh_text = dsh.read_text(encoding="utf-8")
    check("DSH.md 记录 claude-code.md 死链问题", "claude-code.md" in dsh_text)
    check("AGENTS.md 记录非硬约束现状", "不是硬约束" in agents_text)

    # 版本控制：两份本机修正必须已入库、未被忽略、且已进过 HEAD。
    # 只 `git add` 未 commit 时 ls-files 也有值，但重装后无法 git checkout 恢复，
    # 所以 HEAD 这一条是 P0 的真正验收点。
    expected = {".agents/skills/pstack/DSH.md",
                ".agents/skills/pstack/docs/harness/claude-code.md"}
    tracked = {line.strip() for line in git("ls-files", ".agents/").stdout.splitlines()
               if line.strip()}
    check("两份本机修正已入库且仅此两份", tracked == expected, str(sorted(tracked)))
    for rel in sorted(expected):
        short = rel.rsplit("/", 1)[-1]
        check(f"{short} 未被 .gitignore 忽略",
              git("check-ignore", "-v", rel).returncode != 0)
        check(f"{short} 已进 HEAD（可从 git 恢复）",
              bool(git("log", "-1", "--format=%H", "--", rel).stdout.strip()))
    return finish()


def finish() -> int:
    print(f"\n=== {passed} passed, {len(failed)} failed ===")
    for name in failed:
        print("FAIL:", name)
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
