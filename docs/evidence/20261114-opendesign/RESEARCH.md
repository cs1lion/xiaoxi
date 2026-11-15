# OpenDesign 调研与安装记录

- 日期：2026-11-14
- 结论先说：**真正要装的是 `nexu-io/open-design`（本地优先设计工作区 + skills/MCP），不是 npm 上的 `opendesign` 包**。本机已完成「skills 侧」安装并可用；「daemon/桌面端」侧未装（原因见 §3）。

## 1. 命名消歧（重要）

| 候选 | 是什么 | 结论 |
|---|---|---|
| npm `opendesign@0.3.0` | "CLI for working with OpenDesign .octopus files"，2021 创建、**2023-07-31 停更**、Proprietary、依赖 `@opendesign/engine-wasm` | **无关**，不要装 |
| OpenDesign（`nexu-io/open-design`，open-design.ai） | 协作式 AI 设计 Agent 工作区；Apache-2.0；本地优先；108k stars；v0.26 稳定版 | **目标** |

来源：`npm view opendesign`（本机执行）与 <https://github.com/nexu-io/open-design>。

## 2. 它是怎么组织的（决定「怎么用」）

四层可组合资产，全部是「目录 + `SKILL.md`」形式：

| 层 | 作用 | 目录 |
|---|---|---|
| **Skills** | 给 agent 的行为/方法论（本仓库 169 个） | `skills/` |
| **Design templates** | 可渲染的产物蓝图（原型/幻灯片/图/视频） | `design-templates/` |
| **Design systems** | 品牌契约 `DESIGN.md`（151 个包） | `design-systems/` |
| **Plugins** | 可运行工作流 | `plugins/` |

对 agent 的两种接入方式：
1. **MCP**：`od mcp install <agent>`（支持 `opencode`），把本地项目文件以只读结构化 API 暴露给 agent。
2. **Skills**：把 `skills/<x>` 拷进 agent 的 skills 目录，直接按 `SKILL.md` 执行。

## 3. 本机环境探测（先探测再承诺）

| 项 | 实测 | 影响 |
|---|---|---|
| Node | **v22.23.1** | 源码路线要求 **Node ~24** → 不满足 |
| pnpm | **损坏**（`Cannot find module ...pnpm.mjs`） | 源码路线 `pnpm install` 跑不了 |
| corepack | 0.34.6 | 可 `corepack enable` 拉 pinned pnpm，但前提是 Node 24 |
| Docker | CLI 29.8.2 在，**daemon 未运行**（npipe 连不上） | Docker 路线不可用（需先起 Docker Desktop） |
| `od` | `C:\Program Files\Git\usr\bin\od.exe`（Git 自带的八进制 dump） | **官方 `install.sh` 会误判**，见下 |
| 磁盘 | D: 66 GB 空闲 | 足够 |

### 3.1 官方一行安装的真实结果（已复现失败）

`curl -fsSL https://open-design.ai/install.sh | sh -s opencode` 的包装脚本只做一件事：探测 `od` 是否 OpenDesign CLI，不是就报错退出。本机执行（Git Bash）：

```
OpenDesign install.sh: '/usr/bin/od' does not look like the OpenDesign CLI.
... 退出码 1
```

证据：`install-sh-attempt.txt`。**这不是网络问题，是 PATH 上 `od` 被 Git 的八进制工具占位**；脚本自己给的解法是「装桌面端 → Settings → MCP server 复制绝对路径片段」。桌面端是 GUI 安装 + 系统级落盘，本轮**未经你确认不做**。

## 4. 本轮实际安装了什么

用了不依赖 daemon 的那条路：**sparse clone + 拷 skills**。

```powershell
# 1) 仓库只取 skills（blobless，避免拉整仓 100+ MB 资产）
cd tmp
git clone --depth 1 --filter=blob:none --sparse https://github.com/nexu-io/open-design.git open-design
cd open-design
git sparse-checkout set skills

# 2) 选 9 个与本项目相关的 skill 拷进项目 skills 目录（.agents/ 已被 .gitignore 排除，改动可撤回）
Copy-Item -Recurse tmp\open-design\skills\<name> .agents\skills\<name>
```

已安装（`.agents/skills/`，安装后本会话即被识别为新 skills）：

| skill | 体量 | 用途 |
|---|---:|---|
| `redesign-skill` | 16.5 KB | 现有项目升级到「高级感」的审计流程 |
| `frontend-design` | 15.3 KB | 生产级前端视觉方向 |
| `web-design-guidelines` | 10.3 KB + 参考清单 | Vercel Web Interface Guidelines 逐条评审 |
| `gsap-performance` | 6.6 KB | 动画/FPS 优化规则 |
| `impeccable-design-polish` | 2.9 KB | 上线前的打磨/加固清单 |
| `design-review` / `plan-design-review` / `ui-skills` / `color-expert` | 各 ~1.2 KB | **仅目录条目**（见 §5），指向 upstream 仓库 |

## 5. 关键发现：169 个 skill 里 109 个是「目录条目」

用文件数/体积做的客观分类（`skills/` 全量）：

| 分类 | 数量 | 判据 |
|---|---:|---|
| 单文件（`SKILL.md` 只有几行 + 指回 upstream） | **93** | files = 1 |
| 两文件 | 16 | files = 2 |
| 有真实参考资产（references/scripts/assets） | **60** | files ≥ 3 |
| 合计 | 169 | 4242 KB |

也就是说：**OpenDesign 的 skills 目录一半以上是"广告位"**，真正的价值要么在 60 个带资产的 skill 里，要么在桌面端/设计系统/MCP。给项目做优化时我优先用了 `web-design-guidelines` 的规则集（唯一一份可直接执行的 checklists），其余按需。

## 6. 怎么用（可直接复用的三条）

1. **审计现有界面**（本次用的）：`design review` / `web design guidelines` — 读代码 + 截图，按 `file:line` 出问题清单。可执行清单在
   `.agents/skills/web-design-guidelines/references/guidelines.md`。
2. **重设计升级**：`redesign existing project` — 先审计"通用 AI 味"（模板化渐变、无层级、过度圆角），再给具体替换。
3. **动画性能**：`gsap performance` — 只用 transform/opacity、批量读写、避免 layout thrash；与本项目已有的 `scripts/test-performance-probe-browser.mjs` 天然对接。

若要补全 MCP/daemon（需要你拍板）：Node 24 + `corepack enable` → `pnpm install` → `pnpm tools-dev`，或装 Windows 桌面端后在 Settings → MCP server 复制 `opencode` 片段。

## 7. 边界（诚实清单）

- 本机**没有** `od` 可用 → 无法 `od project list` / 无法绑定 `DESIGN.md` / 无本地预览 UI。
- 本轮产出是**方法论 + 清单**级别，不是"OpenDesign 渲染出的设计稿"。
- 抄进来的 skill 不含上游完整脚本资产（部分只有 `SKILL.md`）。
