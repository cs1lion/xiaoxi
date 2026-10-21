# ADR: 世界调色板从 16 色扩展到 24 色

- **状态**：已批准（用户，2026-10-21）
- **决策范围**：`pixel-world` 的世界 canvas 源色；UI 仍 8 色
- **相关**：`ART_SPEC.md` §2.1、`ART_PALETTE_AUDIT.md`、`palette-data.ts`、`prefabs.json`

## 背景

16 色在 P0/P1 阶段足够修好“坐标纸、平面、无深度”，但到材料与光照阶段出现硬缺口：

- 书阁没有浅色灰泥墙，只能用 `timber`，三栋建筑主体同色；绿色屋顶 `grass` 与草地并团。
- 没有可用的水色，雨只能压暗 + 白线，水洼只能用 `cloakDark` 冒充。
- 没有比 `grassLit` 更亮的苔色，石缝/树冠的苔只能靠 `grass`，几乎不可见。
- 没有比 `amber` 更亮的暖高光，窗芯/灯芯/符文只能到 `amber`，缺少“发光”的最后一档。
- 没有冷影与炭黑，檐下阴影只能用 `ink` alpha，烟囱/暗瓦只能用 `timberDark`。

用户 2026-10-21 明确批准 24 色 ADR。

## 决策

世界层从 **16** 扩到 **24**：保留原 16 色语义与色值，新增 8 个材料/光色。UI 8 色不变；世界与 UI 仍只共享 `#e3a13c`；并集 **31**。新增第 25 色必须再次走 ADR + 用户批准。

| 编号 | token | 色值 | luma255 | 角色 |
|---|---|---|---:|---|
| W17 | `plaster` | `#c2b39a` | 179 | 灰泥墙 / 浅纸面；书阁主体，比 `trim` 更中性 |
| W18 | `terracotta` | `#9c4f3c` | 94 | 陶瓦 / 砖；工坊屋顶、干草垛、陶罐 |
| W19 | `copper` | `#5f8a74` | 127 | 铜绿屋顶；书阁塔楼，与草地/木色拉开 |
| W20 | `water` | `#3d6b7a` | 98 | 水；水洼、低雾、冷反光 |
| W21 | `moss` | `#4e6241` | 91 | 苔；石缝、树冠、绿化带 |
| W22 | `deepShade` | `#18242b` | 34 | 冷影；檐下、底部、雾的冷暗层、暗窗 |
| W23 | `warmLight` | `#f2c879` | 203 | 暖高光；窗芯、灯芯、祭坛符文、屋脊受光 |
| W24 | `charcoal` | `#5a463e` | 72 | 炭黑；暗瓦、烟囱、铁器、门体 |

## 使用规则

- **不是把 24 色平均撒进画面**：新增色只服务材料与光，不改变“大面积安静、少量发光”的构图。
- `plaster` 只做被照亮的灰泥/浅纸面，不做地面；`terracotta`/`copper`/`charcoal` 只做屋面/陶/铁，不铺大面积地形。
- `water` 只做水与雾；`deepShade` 只做阴影/暗窗/雾的冷层，不与 `ink` 混用为轮廓。
- `warmLight` 只做发光体最亮一档；单点面积小于 `amber`，避免整屏发白。
- `moss` 只做苔/绿化，不做大面积草地基色。
- alpha 纪律不变：只用于阴影、压暗、脉冲与水/雾的透明层；材质色阶仍用源色。

## 落地

- `palette-data.ts`：新增 W17–W24；`WORLD` 共 24 个 token。
- `prefabs.json`：forge 屋顶 `terracotta`；archive 主体 `plaster` / 屋顶 `copper`；ink house 屋顶 `charcoal`；workshop 保持 `cloak` 蓝顶。
- `buildings.ts`：灰泥/陶瓦/铜绿/炭黑用于墙、瓦、烟囱、门；`warmLight` 做窗芯/屋脊/檐口高光；`deepShade` 做檐下与暗窗。
- `tilemap.ts`：`moss` 做石缝/树冠/绿化；`terracotta` 做干草垛；`deepShade` 做树篱暗缝。
- `weather.ts`：`water` 做水洼与低雾；`deepShade` 做水洼边缘；`warmLight` 做水面暖反光。
- `villager.ts`：阿雾的侧包改用 `terracotta`；阴影外层改用 `deepShade`。
- `SandboxScene.ts`：祭坛光池/符文/烛火用 `warmLight`，苔用 `moss`。

## 验证

- `palette-budget.test.ts`：世界 24 / UI 8 / 共享 1 / 并集 31，逐 token 锁值。
- `scripts/audit-palette.mjs --strict`：世界 24、UI 8、越界 0。
- `npm run check`、`npm test`、`npm run build`、`node scripts/smoke.mjs`。
- 晴/雨 native 480×320 与 400% 截图；人类 3 秒指认仍由用户评审。

## 不做

- 不扩 UI 调色板；不把 UI 色引入 canvas。
- 不做昼夜循环；夜景仍是 1.1 的独立裁决，可在 24 色内重测，但不自动加第 25 色。
- 不引入位图素材或新的绘制管线；本次只扩源色与用途。
- 不改变世界逻辑、碰撞、台账或协议。
