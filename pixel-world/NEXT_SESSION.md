# 像素世界优化 · 交接

写给下一个会话。PR #16 已合并入 master，本文件是下一轮工作的起点。

## 起点状态

- 仓库：`cs1lion/xiaoxi`，master 已含 PR #16（merge commit `d4befc0`）
- 本 worktree：`D:\project\neurosama\.claude\worktrees\optimistic-clarke-71e4fd`
- 工作区干净。`.env` 里有 StepFun key（已 gitignore），服务端用它跑 live 意图评测
- 全量门禁：`npm test` 567/567、`test_core.py` 92/92、`test_world_intent.py` 28/28

## 用户选定的四件事（都做）

用户原话「都想要」。按依赖顺序做，每件都能单独验收：

### 1. 让镇子更满

**问题（已实测）**：96×72 的地图只放 6 栋楼。镇内四块各 10×10，每块只放 1 栋；
楼是 4×5 格，即每块 100 格里只占约 20 格。郊野同样。

**关键文件**：
- `src/world/setup.ts:54` `LARGE_WORLD_SPEC`（分区表）
- `src/world/setup.ts:88` `TOWN_PREFABS` / `WILD_PREFABS`（谁进开局）
- `src/world/prefabs.json` — 7 个模板，其中 `workshop_plot` 是不接活的扩建样板
- `src/world/setup.ts:208` `buildWorld` — 自动落位逻辑

**注意**：不能简单多塞楼。`buildWorld` 会跑 `validateCoverage`，要求
「每类活 1 或 2 栋（镇内 + 郊野）」。加楼要同步改这条约束与测试。
另外 `checkPlacement` 有迷雾、占地、留白、门口四道校验，手动摆位置会撞。

### 2. 丰富地表层次

**现状（已实测）**：`src/render/tilemap.ts:483-500` 草地细节概率是
`h % 53`（岩石）、`h % 71`（灌木）、`h % 37`（石板点），合计约 5%。
大片草地基本是纯色。

**方向**：按群落分化地表。镇内石板缝长苔、农田有垄沟、郊野有野花。
纯渲染层改动，不碰模拟，风险最低。

**约束**：24 色冻结调色板（`src/world/palette-data.ts` 的 `WORLD`），
alpha 只允许阴影/压暗/脉冲。改完必须跑 `node scripts/audit-palette.mjs --strict`。

### 3. 世界的日常动静

**现状**：除了 2 个村民和 1 只猫，白天没有别的东西在动。

**已有可复用的东西**：
- `src/render/phase-view.ts:191` `drawFireflies` — 萤火虫，夜里才出现
- `src/world/simulation.ts` 的 `stepCritters` — 猫的游走逻辑
- `src/render/decor.ts:212` `drawCat` — 猫的画法

**注意**：新增会动的东西要过省电模式（`src/render/eco-mode.ts`）
与渲染预算（`scripts/check-render-budget.mjs`，要求拖动时帧率不明显低于静止）。

### 4. 意图层：把世界状态喂给模型

**现状（已写进 `docs/WORLD_INTENT.md` 的「已知边界」）**：模型只看到这句话
与模板表，不知道镇上几栋楼、谁在忙。「建个工坊」若已有工坊，由世界在结算时
拒绝并说清，而不是模型提前判断。

**关键文件**：
- `pixel-world/src/world/intent-client.ts` — 请求体组装（现在只发 utterance + templates）
- `server/world_intent.py` — 服务端提示词与端点
- `pixel-world/src/world/world-state.ts` — `summarizeWorld` / `describeWorldState`，已有摘要

**设计约束**：`docs/WORLD_INTENT.md` §二 的管道不能变——规则优先、模型兜底、
失败落 unknown。改的时候别把规则层绕过去。

## 必读文档

| 文档 | 讲什么 |
|---|---|
| `AGENTS.md`（仓库根） | 项目规矩、验证命令、poteto-mode 路径 |
| `docs/WORLD_INTENT.md` | 意图层设计与实测结果 |
| `docs/LARGE_WORLD.md` | 大世界规格与已踩过的坑 |
| `docs/ADR_WORLD_ALIVE.md` | 四刻、迷雾、痕迹的决策依据 |
| `docs/ASSET_PIPELINE.md` | 美术资产约束 |

## 验证命令

```bash
cd pixel-world
npm run check && npm test
node scripts/audit-palette.mjs --strict
npm run build
```

浏览器回归（先起 dev server）：

```bash
PIXEL_WORLD_URL=http://127.0.0.1:5199/ node scripts/smoke.mjs
PIXEL_WORLD_URL=http://127.0.0.1:5199/ node scripts/check-phases.mjs
PIXEL_WORLD_URL=http://127.0.0.1:5199/ node scripts/check-creation.mjs
PIXEL_WORLD_URL=http://127.0.0.1:5199/ node scripts/check-render-budget.mjs
PIXEL_WORLD_URL=http://127.0.0.1:5199/ node scripts/check-persistence.mjs
```

根目录：

```bash
python scripts/check_protocol.py
D:/project/neurosama/.venv/Scripts/python.exe scripts/test_core.py
D:/project/neurosama/.venv/Scripts/python.exe scripts/test_world_intent.py
```

live 意图评测（需要 key，服务要起在 8011）：

```bash
D:/project/neurosama/.venv/Scripts/python.exe scripts/eval_world_intent_live.py --port 8011
```

## 已踩过的坑（别重复）

- **浏览器回归脚本的等待条件**：`#reply` 在模型路径下会先显示「正在问模型……」。
  等回执时不能只等「和上次不同」，要等**稳定 3 秒不变**，否则会读到中间态。
- **改 `web/` 或 `pixel-world/dist` 后必须重启服务**，StaticFiles 有缓存。
- **`/world` 挂载在 import 时决定**：`server/static.py:25` 检查 `dist/index.html`
  存在才挂。先 `npm run build:world` 再起服务。
- **调色板不能加颜色**：属项目红线，须 ADR + 用户批准。
- **`pixel-world/src/world/` 不许 import Phaser**：渲染的东西放 `src/render/`。

## 本会话做过的、别推翻的决定

- **规则优先，模型兜底**（`docs/WORLD_INTENT.md` §一）。不是全交给模型。
  离线可跑、确定性优先、已稳定的行为不因接模型而改变。
- **失败等于没听懂**。超时、非 JSON、编造模板一律落 `unknown`，绝不猜。
- **`max_tokens` 1024 + `reasoning_effort=low`**。实测 200 会被推理模型吃光，
  正文被截断或为空。见 `docs/WORLD_INTENT.md` §七。
- **提示词有两处实现**（`intent-prompt.ts` 与 `world_intent.py`），
  都由模板表驱动。改措辞要两边一起改。
