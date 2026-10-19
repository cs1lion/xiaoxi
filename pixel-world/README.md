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
npm test           # 纯逻辑，139 项，不需要浏览器
npm run check      # 类型检查
npm run build      # tsc + vite build
```

浏览器回归：

```bash
cd pixel-world
node scripts/smoke.mjs              # 沙盒闭环，114 项，不依赖本地服务
LEDGER=1 node scripts/smoke.mjs     # 台账连通，21 项，需先起 8000
```

`LEDGER=1` 那一档验的是"操作台与世界连通"：世界从 `GET /api/executive/commissions`
拉到全量，之后靠 WS 的 `commission_update` 增量推进；脚本用一帧注入的督办验
"上游派活 → 她动起来"和"上游收工 → 她交付"。

两种模式都靠 `?ledger=off` / `?ledger=on` 切换，默认接台账；离线演示用 `off`。

调色板截图（晴 / 雨 / 夜三态，可复现）：

```bash
# 1. 另开终端：起世界
cd pixel-world && npm run dev
# 2. 另开终端：起浏览器（脚本不自己 launch，沙箱里会 EPERM）
chrome-headless-shell --headless --remote-debugging-port=9333 --no-sandbox \
  --enable-unsafe-swiftshader --window-size=1010,1400 about:blank
# 3. 截图
cd pixel-world && node scripts/capture-palette.mjs
```

产物在 `eval/screens/palette-{sunny,rain,night}-{100,canvas,native-480x320,400pct}.png`，
同种子复跑字节一致。前置、种子、方法与常见报错见 [docs/E2E_PALETTE.md](docs/E2E_PALETTE.md)。

## 目录与职责

```
src/world/          纯逻辑，不 import Phaser，可被 node --test 直接加载
  types.ts          数据模型。世界只认这里定义的状态，渲染层不许自创字段
  grid.ts           网格账本：分区、占用、留白、A*、门口可通行
  blueprints.ts     蓝图 JSON 的边界解析，非法蓝图直接拒绝开工
  setup.ts          世界规格（尺寸/分区/预算）、落位、自动铺路、不变量校验
  oracle.ts         一句话 → 派活 / 交付 / 天气 / 认不出
  simulation.ts     状态机 + 命令队列。固定步长，无内部时钟，可确定性回放
  projection.ts     台账 → 世界事实。唯一的映射点
  ledger.ts         台账客户端：REST 全量 + WS 增量 + 断线兜底
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
test/               139 项纯逻辑用例（522 条断言）
scripts/smoke.mjs   浏览器闭环回归
```

## 三条硬约束（改代码前先读）

1. **逻辑与渲染分离。** `src/world/` 里出现 `import Phaser` 就是错的。所有空间判断在
   `WorldGrid` 结算，渲染层不判断"能不能走"。
2. **命令只有一个入口。** 外部只能 `sim.enqueue()`。命令在下一个逻辑拍开头统一结算，
   所以渲染循环里不会看到半更新的世界。`say()` 返回的"已投递"不代表世界照办了，
   真正的回执走 `onResult`。外部 Agent 走 `feed()`，台账走 `sync`，同样落到这条队列。
3. **中文不画进 canvas。** canvas 只画世界，状态行、回执、报纸、按钮全在 DOM。
   像素中文低于 12px 不可读，长文本必须留在纸面。

## 接台账时的四条纪律

1. **世界不编进度。** 台账的活一律 `determinate: false`，铺子上方不画百分比。
   活跑多久只有台账知道，计时器估出来的那条线是假的。
2. **完成权在台账手里。** 台账说收工她才交付；台账撤单她就停手。沙盒的活才由计时器收尾。
3. **台账是唯一真相。** 世界只读不写，不新建督办、不改状态。断线时照常过日子，
   但状态行必须写"台账离线"。
4. **映射只在一处。** `projection.ts` 是台账字段 ↔ 世界活类的唯一映射点，
   换台账只改那里。

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
- 造物主的 LLM 生成。扩建走的是固定样板 + `findBestSlot`，生成层留着没做。
- 多角色。只有一个她；7 个 Agent 的活都归到她一个人头上。
- 回写。世界是纯投影，验收/打回/换将这些动作还在纸面做。

## 下一步的岔口

1. **像素化 3D**：现有 VRM 渲进 48×64 render target，量化 16 色，接进来替换色块小人。
   这一步决定美术路线是否成立，是架构笔记里的 E2 实验。
2. **多角色**：一个铺子一个店主，7 个 Agent 各自有人。当前是一人干全部活，
   台账一忙就会露怯。
3. **世界里的动作**：在镇子里点一下铺子就能验收/打回，走
   `POST /api/executive/commissions/{id}/accept`。需要先定同源策略。
4. **造物主接 LLM**：只让模型输出 `{ prefab_id, placement, palette }`，落位交给
   `findBestSlot`。解析、校验、施工、铺路都已就位，只差生成侧。
