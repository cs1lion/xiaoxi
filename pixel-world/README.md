# 莞尔的世界 · 神谕沙盒原型

独立原型，不属于主仓库的零构建前端（`web/`）。这里可以有 `package.json`、Vite 和
`node_modules`；主库一行不受影响。

## 这个原型在验什么

不是"像素画得好不好"，而是四个能否成立的判断：

1. **一句神谕能不能变成一次可见的具身行动**（停下、行礼、走向对应铺子、机器开工）。
2. **世界能不能挂在一份纯逻辑真相上**，渲染只读快照，换个渲染器不用改世界。
3. **玩家不在时的自由度能不能不烧 token**（本地作息跑日常，模型只在神谕时介入）。
4. **世界能不能不撒谎**：每个动画都能追到一个真实字段，没发生的事不写进日报。

## 跑法

```bash
cd pixel-world
npm install
npm run dev        # http://127.0.0.1:5188
```

```bash
cd pixel-world
npm test           # 纯逻辑，45 项，不需要浏览器
npm run check      # 类型检查
npm run build      # tsc + vite build
```

浏览器回归（需要 dev server 已起，且主仓库根目录装过 Playwright）：

```bash
cd pixel-world
node scripts/smoke.mjs
```

它跑完整闭环并断言 50 条用户可观察的行为，截图落在 `eval/screens/`。

## 目录与职责

```
src/world/          纯逻辑，不 import Phaser，可被 node --test 直接加载
  types.ts          数据模型。世界只认这里定义的状态，渲染层不许自创字段
  grid.ts           网格账本：分区、占用、留白、A*、门口可通行
  blueprints.ts     蓝图 JSON 的边界解析，非法蓝图直接拒绝开工
  setup.ts          世界规格（尺寸/分区/预算）、落位、自动铺路、不变量校验
  oracle.ts         一句话 → 派活 / 交付 / 天气 / 认不出
  simulation.ts     状态机 + 命令队列。固定步长，无内部时钟，可确定性回放
  tasks.ts          长短任务分级：短活原地办，长活进城
  routines.ts       日常作息：没活干时她去哪，不花 token
  weather.ts        天气浓淡与自动退场，渐变不秒切
  faith.ts          信仰结算：只让世界多一件东西，不发货币
  report.ts         神谕日报，从快照推导，不另存真相
  bridge.ts         外部 Agent 的双向消息形状与转换
  prefabs.json      蓝图注册表，改世界不用改代码
src/render/         只读 WorldSnapshot，画地形、建筑、小人、烟、雨、脚手架、摆件
src/scenes/         唯一的 Phaser 场景：驱动 tick、转输入、转快照
src/ui/             DOM 神谕面板与报纸。中文永远归 DOM，不画进 canvas
src/audio/          哇啦哇啦拟音，Web Audio 现场合成，无素材、无 TTS
test/               45 项纯逻辑断言
scripts/smoke.mjs   浏览器闭环回归
```

## 三条硬约束（改代码前先读）

1. **逻辑与渲染分离。** `src/world/` 里出现 `import Phaser` 就是错的。所有空间判断在
   `WorldGrid` 结算，渲染层不判断"能不能走"。
2. **命令只有一个入口。** 外部只能 `sim.enqueue()`。命令在下一个逻辑拍开头统一结算，
   所以渲染循环里不会看到半更新的世界。`say()` 返回的"已投递"不代表世界照办了，
   真正的回执走 `onResult`。外部 Agent 走 `feed()`，同样落到这条队列。
3. **中文不画进 canvas。** canvas 只画世界，状态行、回执、报纸、按钮全在 DOM。
   像素中文低于 12px 不可读，长文本必须留在纸面。

## 世界预算（写成常量，不许破）

- 30×20 格，16px/格，逻辑分辨率 480×320，固定 2 倍整数放大
- 7 个分区，3 栋常驻铺子 + 1 种扩建样板，1 个角色，4 类活，每栋 1 个工位
- 同时只能接一单；短活不占铺子；铺子四态（静息 / 运转 / 故障 / 冷却）
- 调色板来自 `src/render/palette.ts` 与蓝图，不允许渲染层随手取色

`assertWorldInvariants()` 在开工前检查：接活铺子数量、分区不重叠、门口不在祭坛区、
每栋都能从祭坛走到。不合规直接抛错，不允许带着半个世界跑。

## 这一版**没有**做的

- 昼夜与 2D 光照。天气已有（雨/晴），但没有时段与灯光。
- 真实的工具失败。工坊有 `overheating` 态，但 `shouldOverheat()` 恒为 false：
  没有真实工具时伪造失败就是假动作。
- 接真实台账（`/api/executive/commissions`）。当前是自洽沙盒，
  `Bridge` 与 `WorldCommand` 已经就位，接台账时只换运输层。
- 造物主的 LLM 生成。扩建走的是固定样板 + `findBestSlot`，生成侧没做。
- 多角色。只有一个她。

## 下一步的岔口

1. **接真实台账**：把 commission 六阶段映射到 `VillagerMode` 与 `WorkstationActivity`，
   沙盒的队列接上 WS。这一步决定这个世界是"玩具"还是"产品的一扇窗"。
2. **像素化 3D**：现有 VRM 渲进 48×64 render target，量化 16 色，接进来替换色块小人。
   这一步决定美术路线是否成立，是架构笔记里的 E2 实验。
3. **造物主接 LLM**：只让模型输出 `{ prefab_id, placement, palette }`，落位交给
   `findBestSlot`。解析、校验、施工、铺路都已就位，只差生成侧。
