# 莞尔的世界 · 神谕沙盒原型

独立原型，不属于主仓库的零构建前端（`web/`）。这里可以有 `package.json`、Vite 和
`node_modules`；主库一行不受影响。

## 这个原型在验什么

不是"像素画得好不好"，而是三个能否成立的判断：

1. **一句神谕能不能变成一次可见的具身行动**（停下、行礼、走向对应铺子、机器开工）。
2. **世界能不能挂在一份纯逻辑真相上**，渲染只读快照，换个渲染器不用改世界。
3. **玩家不在时的自由度能不能不烧 token**（本地行为树跑日常，模型只在神谕时介入）。

## 跑法

```bash
cd pixel-world
npm install
npm run dev        # http://127.0.0.1:5188
```

```bash
cd pixel-world
npm test           # 纯逻辑，22 项，不需要浏览器
npm run check      # 类型检查
npm run build      # tsc + vite build
```

浏览器回归（需要 dev server 已起，且主仓库根目录装过 Playwright）：

```bash
cd pixel-world
node scripts/smoke.mjs
```

它跑完整闭环并断言 25 条用户可观察的行为，截图落在 `eval/screens/`。

## 目录与职责

```
src/world/        纯逻辑，不 import Phaser，可被 node --test 直接加载
  types.ts        数据模型。世界只认这里定义的状态，渲染层不许自创字段
  grid.ts         网格账本：分区、占用、留白、A*、门口可通行
  blueprints.ts   蓝图 JSON 的边界解析，非法蓝图直接拒绝开工
  setup.ts        世界规格（尺寸/分区/预算）与落位，含不变量校验
  oracle.ts       一句话 → 四个动词。认不出就落 unknown，世界不动
  simulation.ts   状态机 + 命令队列。固定步长，无内部时钟，可确定性回放
src/render/       只读 WorldSnapshot，画地形、建筑、小人、烟
src/scenes/       唯一的 Phaser 场景：驱动 tick、转输入、转快照
src/ui/           DOM 神谕面板。中文永远归 DOM，不画进 canvas
src/world/prefabs.json  蓝图注册表，改世界不用改代码
test/             22 项纯逻辑断言
scripts/smoke.mjs 浏览器闭环回归
```

## 三条硬约束（改代码前先读）

1. **逻辑与渲染分离。** `src/world/` 里出现 `import Phaser` 就是错的。所有空间判断在
   `WorldGrid` 结算，渲染层不判断"能不能走"。
2. **命令只有一个入口。** 外部只能 `sim.enqueue()`。命令在下一个逻辑拍开头统一结算，
   所以渲染循环里不会看到半更新的世界。`say()` 返回的"已投递"不代表世界照办了，
   真正的回执走 `onResult`。
3. **中文不画进 canvas。** canvas 只画世界，状态行、回执、按钮全在 DOM。
   像素中文低于 12px 不可读，长文本必须留在纸面。

## 世界预算（写成常量，不许破）

- 30×20 格，16px/格，逻辑分辨率 480×320，固定 2 倍整数放大
- 6 个分区，3 栋铺子，1 个角色，3 类活，每栋 1 个工位
- 调色板来自 `src/render/palette.ts` 与蓝图，不允许渲染层随手取色

`assertWorldInvariants()` 在开工前检查：铺子数量、分区不重叠、门口不在祭坛区、
每栋都能从祭坛走到。不合规直接抛错，不允许带着半个世界跑。

## 这一版**没有**做的

- 天气、昼夜、2D 光照（架构笔记里的 Phase 4）
- 神谕日报、哇啦哇啦拟音、信仰结算（Phase 6）
- 接真实台账（`/api/executive/commissions`）。当前是自洽沙盒，
  `Simulation` 的输入口已经收成 `WorldCommand`，接台账时只换投递方
- 造物主的 LLM 生成。蓝图是手写的，生成器层留着没做

## 下一步的三个岔口

1. **接真实台账**：把 commission 六阶段映射到 `VillagerMode` 与 `WorkstationActivity`，
   沙盒的命令队列换成 WS 事件。这一步决定这个世界是"玩具"还是"产品的一扇窗"。
2. **像素化 3D**：现有 VRM 渲进 48×64 render target，量化 16 色，接进来替换色块小人。
   这一步决定美术路线是否成立，是架构笔记里的 E2 实验。
3. **造物主**：LLM 输出 `{ prefab_id, placement, palette }`，走 `findBestSlot` 落位。
   `Blueprints` 的解析与校验已经就位，只差生成侧。
