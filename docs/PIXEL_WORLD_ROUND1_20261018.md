# 像素世界原型：本轮决策留痕

2026-10-18。按用户的三项选择执行：独立原型目录、神谕闭环 MVP、全新角色（阿栖的 3D 设定不进这个世界）。

## 一、为什么不是照着 Gemini 工单直接下手

工单要求 `npm create vite` 新建工程 + Phaser 3。仓库里已有 `pixel-world/` 空壳（只有 `types.ts` 与 `grid.ts`），
说明这条路线此前已经选定，本轮是接着做，不是重开。

真正要处理的是三条与仓库硬约束的冲突：

| 冲突 | 工单怎么说 | 仓库怎么说 | 本轮处理 |
|---|---|---|---|
| 构建步骤 | Vite 工程 | `web/` 零构建，50+ 浏览器测试依赖 | 原型留在 `pixel-world/`，`web/` 一行不动 |
| 真相源 | 自洽沙盒 | 台账在 Python，世界应是投影 | 先用自洽沙盒验伪；输入口收成 `WorldCommand`，接台账只换投递方 |
| 文字位置 | 画进 canvas | 文字归纸面 DOM | canvas 只画世界；状态行、回执、按钮全在 DOM |

## 二、这轮实际做的

纯逻辑层（不 import Phaser，可离线测）：

- `types.ts`：世界数据模型。`VillagerMode` 六态（后续新增 `sheltering`，现为七态，见 `src/world/types.ts`）、`WorkstationActivity` 四态、`OracleIntent` 三态，全部判别联合。
- `grid.ts`：分区、占用、留白、4 邻域 A*、门口可通行。
- `blueprints.ts`：蓝图表在边界解析。id 不符、门口落在占地内、活与铺子对不上、每种活不是恰好一栋，全部拒绝开工。
- `setup.ts`：世界规格（30×20、16px、6 分区、3 铺子）与落位。`assertWorldInvariants` 检查铺子数量、分区不重叠、门口不在祭坛、每栋都能从祭坛走到。
- `oracle.ts`：一句话收敛到「派活 / 交付 / 认不出」。认不出的落 `unknown`，世界不动。
- `simulation.ts`：状态机 + 命令队列。外部只能 `enqueue()`，命令在下一个逻辑拍开头统一结算，所以渲染循环看不到半更新的世界。无内部时钟，可确定性回放。

渲染层（只读快照）：

- `tilemap.ts` 地形与装饰，`buildings.ts` 三种剪影（尖顶 / 塔楼 / 平顶），`villager.ts` 16×32 色块精灵，`smoke.ts` 按 accent 分三种粒子。
- `SandboxScene.ts` 固定 10Hz 逻辑步长，每帧读快照重画；不做任何业务判断。

## 三、验证

| 门 | 命令 | 结果 |
|---|---|---|
| 类型 | `npm run check` | 通过 |
| 逻辑 | `npm test` | 22 项通过 |
| 构建 | `npm run build` | 通过 |
| 浏览器闭环 | `node scripts/smoke.mjs` | 25 项通过，截图在 `eval/screens/` |
| 仓库协议 | `scripts/check_protocol.py` | PASS |
| 仓库基线 | `scripts/test_core.py` | 64 passed |
| 仓库前端 | `npm test`（根目录） | 退出码 0 |
| 仓库评测 | `scripts/run_evals.py` | 6 passed |

浏览器回归断言的是用户能看见的事，不是内部调用：开局她自己在走、认不出的话不触发神谕、
行礼 → 进铺子 → 机器运转且只有一台、自动交付 → 冷却 → 回祭坛、手上有活时新神谕被拒且理由回到界面、
人工交付也能结束这单。

## 四、过程中改掉的两个真实缺陷

1. **界面抢答。** 第一版 `say()` 在投递时就回「神谕已送达」，但世界可能在下一拍拒单。
   浏览器测试恰好抓住了这条：断言通过时界面在说谎。改成 `say()` 只表示"认出了这句话"，
   世界的回执走 `onResult` 订阅。队列语义没动。
2. **蓝图被 gitignore 吞掉。** `prefabs.json` 原本放在 `src/data/`，被根 `.gitignore` 的 `data/`
   规则静默忽略。蓝图是源码不是运行时数据，已挪到 `src/world/prefabs.json`。

## 五、这一版没做的，与下次从哪继续

没做：天气/昼夜/光照、日报与拟音、接真实台账、造物主的 LLM 生成。

三个岔口按价值排序：

1. **接真实台账**：把 commission 六阶段映射到 `VillagerMode` 与 `WorkstationActivity`。
   这一步决定世界是玩具还是产品的一扇窗。
2. **像素化 3D**：现有 VRM 渲进离屏 render target，量化 16 色。
   这一步决定美术路线是否成立（架构笔记里的 E2），也是唯一能否决整个方向的实验。
3. **造物主**：LLM 只输出 `{ prefab_id, placement, palette }`，落位交给 `findBestSlot`。
   解析与校验已经就位，只差生成侧。
