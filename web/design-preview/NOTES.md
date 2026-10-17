# 下一版 UI 设计探索 · 设计说明

> 阶段：审查 + 方向探索。未改任何业务逻辑、API、状态机与生产界面。
> 预览入口：`/static/design-preview/compare.html`（本地静态服务 `web/` 目录即可打开）。

---

# 第二轮：Midnight Living Stage 实施报告（已落生产）

> 方向 A 已确认并实施。本阶段改动了生产界面（`style.css` / `index.html` + 新增 `stage-fx.*`），未动任何业务逻辑、API、状态机与 3D 渲染层。

## 第三轮：三态胶囊 / Holo-Deck / Aqi-Blocks 质感升维（已落生产）

原生 CSS tokens 集中在 `style.css :root`（`--aqi-*` 一组：玻璃材质、折射 rim、圆角法则、四色语义色晕、精密数字、弹簧曲线、45ms 阶梯），零构建零依赖。

- **三态胶囊**（companion.css）：`rgba(22,24,32,.48)` + `blur(24px) saturate(180%)` + Specular Rim 复合内阴影；悬停上浮 1px、按压 scale(.96)；灵动=金 / 静默=银 / 无痕=薄荷，状态色只在呼吸圆点与色晕上表达。
- **积木卡**（六种 + memo）：深邃基底 `rgba(17,19,26,.78)` + 顶部径向环境光 + 2px 左光条；tone=gold/red/mint/neutral → `--aqi-accent/--aqi-aura` 切换；标题 #f3f4f6 / 副文本 #9ca3af / 时间戳 #64748b；metric 数字与泳道状态强制 tabular-nums + 等宽栈；`R18/R10` 同心圆角。
- **阶梯入场**（blocks.js `createHoloDeck.render`）：按批写入 `--aqi-i`，CSS `animation-delay: calc(var(--aqi-i)*45ms)`，第 3 张卡实测 delay 90ms。
- **测试同步**（不改业务语义）：`test_blocks.mjs` 从过期的 `stage_float` 信封对齐到服务端实际的 `stage_holo + source`（修掉了进入本任务前已存在的红灯）；胶囊材质断言从 blur(10px) 升到新材质。
- **验收**：`check_protocol.py` PASS；`npm test` 全绿（含 test:blocks 4/4）；`test-presence-capsule-browser` 9/9（含「顶栏 0 新增按钮」断言）；`test-aqi-blocks-browser` 9/9（含 XSS/回收/抽屉联动）；桌面与 390 窄屏均无横向溢出、输入框命中正常。证据：`shots/aqi-upgrade-{desktop,narrow}.png`。

## 改动文件

| 文件 | 改动 |
|---|---|
| `web/style.css` | `.stage` 背景加右侧光场与地平线纵深；`.avatar-stage` 响应式尺寸（`clamp()`）+ 深色接触影 + 暖色光池 + 扩大靛蓝光场；`.chat` 右 padding 300→320px；1500px/1100px 断点随动 |
| `web/index.html` | 引入 `stage-fx.css` 与 `stage-fx.js`（两行） |
| `web/stage-fx.css`（新） | 状态光场：按 `#avatarStage[data-interaction]` 分档调立绘光晕/光池强度；空闲呼吸光；失败态压暗而非报警 |
| `web/stage-fx.js`（新） | Canvas 2D 氛围层：≤12 稀疏慢速粒子（窄屏 6）、倾听光环（读 `meterFill` 真实 RMS）、思考汇聚、任务执行流动轨迹（读 Holo-Deck running lane）、卡片送达一次性星屑 |
| `web/design-preview/verify-stage.mjs`（新） | 状态/布局/降级自动验证脚本 |

## 状态映射（全部真实状态源，零新增业务状态）

| 状态 | 来源 | 舞台表现 |
|---|---|---|
| 空闲 / 连续监听 | `data-interaction=idle/monitoring` | 低强度呼吸光（6.5s）+ 慢漂浮粒子 |
| 倾听 | `listening` + `meterFill` 宽度（真实麦克 RMS） | 局部暖光环随音量呼吸 |
| 思考 / 准备 | `thinking` / `preparing` | 粒子向立绘轻微汇聚，亮度微升 |
| 说话 | `speaking` | 冷调光环 |
| 任务执行 | Holo-Deck `.aqi-lane[data-status=running]` | 舞台右缘一条 6s 的节制流动轨迹 |
| 完成 | Holo-Deck 新 `.aqi-block` 送达 | 一次性星屑（800ms，不循环） |
| 失败 / 不可用 | `unavailable` | 光场压暗、画布隐藏；错误照常由状态文案与控件表达 |

## 渲染架构决策

- **未新增第二个 WebGL renderer**：氛围层用 Canvas 2D（约 200 行、零依赖）。VRM 仍由 `avatar/render/vrm.js` 的既有 renderer/scene/四灯渲染，3D 资产照常从站内 `/static/models/*.vrm` 加载（验证中确认 VRM canvas 在位）。
- 全页动画循环仍是两条各自独立的既有 rAF（Avatar 一条）+ 新增氛围层一条；氛围层 `document.hidden` 即停、`prefers-reduced-motion` 不启动（CSS 静态光场兜底）、DPR 上限 1.5、粒子 ≤12。
- 地面阴影用 CSS 双层椭圆（接触影 + 光池）而非 three shadow plane：对 SVG/VRM 两种渲染模式都生效，不碰渲染层，风险最小；后续要更真实的投影可在 vrm.js 加 shadow plane（已留评估结论）。
- 画布 `pointer-events:none`、`z-index:0`，位于星野之上、内容之下；输入框 elementFromPoint 命中验证通过。

## 验证结果

- `verify-stage.mjs`（Playwright，项目既有 devDependency）：桌面 1440×900 与窄屏 390×844 —— 画布挂载、无遮挡、无横向溢出、立绘与标题（字形 Range）无重叠、四种交互态 + 任务执行态注入截图、无控制台错误、reduced-motion 画布静止、正常模式画布在动 —— **全部通过**。
- `npm test`：test:voice / test:calibration / test:vrm / test:performance-probe / starmap / executive-state 全部通过；`test:blocks` 2 项失败为**既有失败**（blocks.js 等文件本身未提交、处于在途状态，stash 掉我的改动后同样失败）。
- 截图证据：`shots/prod-before-desktop`（优化前）、`shots/prod-after-desktop`（优化后 idle）、`shots/prod-state-{listening,thinking,speaking,unavailable,running}.png`、`shots/prod-after-narrow.png`、`shots/before-after.png/stitch.png` 对比拼图。
- 观看入口：`python scripts/run.py` 后开 http://127.0.0.1:8000/（验证期间服务保持运行）。

## 未解决与风险

- 本会话模型无图像输入，状态截图未做逐像素人眼复核；光晕/粒子浓度如觉偏弱或偏强，调 `stage-fx.css` 的 `--halo-o` 档位和 `stage-fx.js` 的粒子参数即可。
- 倾听光环的音量取自 `meterFill` 宽度（真实 RMS 的派生），非 analyser 直读；精度足够做氛围，不做精确电平。
- VRM 模式下 `overflow:visible` 的光晕在超窄屏（≤760）被 `height:190px` 限制，观感以真机为准。

## 1. 对真实项目的理解（不看截图猜结构，全部来自代码）

- **外壳**：`web/index.html` 单页，`.app` 两栏网格（侧栏 304px + 主舞台）。主舞台内：顶栏（连接状态 / 一起写 / 设置）、`.room-label`、`.avatar-stage`（绝对定位右侧，240px 宽、距底 205px）、`#holoDeck`（全息卡片区）、`#companionMount`（共同事项面板，预留 138px）、对话流、底部胶囊输入区（麦克风 / 停止 / 输入 / 工作台抽屉）。
- **设计 token**：`web/style.css :root` —— 夜航星图体系：`--bg:#141c2e`、`--panel:#1c2740`、`--accent:#c9a227`（微光金）、`--star:#e8c26a`、圆角四档、字号三档；星野与星座用 `.stage::before/::after` 纯 CSS 绘制，低密度、不动。
- **VRM 加载**：`web/avatar/render/vrm.js` `VrmRig.mount(container,{url})` → GLTFLoader + three-vrm；`WebGLRenderer({alpha:true})` 透明底，Hemisphere + key + fill + rim 四灯（rim 参数来自 `config/avatar.yaml` stage.lights）。**没有地面阴影**，贴地感目前靠 `.avatar-stage::before` 的 CSS 椭圆光斑。
- **动画循环**：`web/avatar/index.js` 唯一一条 rAF（`dt` clamp 0.05s），口型、待机、视线、手势全在这一帧里驱动；页面另有点击/鼠标回神接口 `onUserInteraction`。
- **真实运行状态**：连接态（`#statusDot` 三态）、`#interactionState` 文案、陪伴三态胶囊（灵动/安静/关闭）、录音电平 `.meter-fill`、模式 chips、Holo-Deck 六种积木卡、工作台抽屉里督办/战况/遥测（executive V3）。
- **已有的探索设施**：`?explore=1` 挂载 `explore.js` 效果混搭层（环境光/纸感/玻璃/光环/弹性）——本次三个方向与它的结论可以互相印证，不冲突。

## 2. 问题分类

**纯 UI 问题（预览页直接表达）**
- 立绘是「浮在空处的贴图」：只有一团金色光斑当影子，没有地面、没有光源逻辑，人物辨识度与空间感都弱。
- 欢迎区、对话、共同事项面板、Holo-Deck 都挤在中左区域，视觉焦点分散；星野星座是均匀铺的背景，不服务于层级。
- 顶栏只是状态文字，任务执行中的进度没有常驻位置（要看只能拉开工作台抽屉）。

**需要复用现有状态的交互问题（本阶段不动，只标注挂点）**
- 舞台状态文案应继续吃 `#interactionState`；A/B 方案里它从主区移到舞台下方，仍由同一状态源驱动。
- C 方案顶栏任务条 = `executive.js` 的 commissions/telemetry 已有数据的摘要视图，不新增状态。
- B 方案记忆细轨展开层 = 现有 `.side` 内容的换位呈现，数据源不变。

**需要触及 3D 渲染层的视觉问题**
- 脚下真实投影：CSS 椭圆只是近似；方向 A 落地时建议在 three 场景加 ContactShadow（或一张径向渐变 shadow plane 贴在 y=0），复用现有 rAF，不加第二条循环。
- 环境光与界面光场联动：rim light 颜色/强度已有配置通道（`config/avatar.yaml`），可随方向换参数，不改加载链路。

## 3. 三个方向（与基线同一份内容，构图实质不同）

| | 骨架 | 视觉焦点 | 排版 | 立绘位置 |
|---|---|---|---|---|
| 基线 | 两栏 + 绝对定位立绘 | 欢迎标题 | 衬线大标题 + 按钮列 | 右侧 240px 悬浮 |
| A Midnight Living Stage | 三栏：记忆 / 内容 / 42% 贯通舞台 | **舞台与人物** | 标题让位给舞台，内容列收窄 | 舞台主角，有地面与光场 |
| B Ambient Companion | 细轨 + 房间：立绘居中偏右，文本流贴左侧 | **人物与话语的关系** | 建议项退成轻声文本行 | 房间主人，体量最大 |
| C Precision Cockpit | 三栏仪器：导航任务 / 对话 / 锚点+指标 | **任务状态条** | 网格 + 等宽数字 | 右栏锚点，体量收敛 |

### A · Midnight Living Stage（午夜数字工作室）
北极星：舞台属于她，界面是观众席。保留深蓝底，粒子更稀（6 颗），新增脚下实投影与身后局部光场，Holo 卡片落在舞台侧翼。
- 优点：立绘辨识度提升最大；保留现有品牌色与氛围，老用户不陌生；舞台区天然承接 Holo-Deck。
- 缺点：三栏在 1100px 以下开始紧张；内容列变窄，长对话阅读宽度变小。
- 实现风险：低-中。真实投影要碰渲染层（shadow plane）；其余都是 CSS 重排。窄屏已处理（舞台退为背景层 + 渐隐遮罩，输入常驻）。

### B · Ambient Companion（陪伴型助手空间）
北极星：不是仪表盘，是一个房间。去面板化：侧栏收成 76px 细轨，欢迎语与快捷操作退成文本行，唯一「实」的控件是底部悬浮输入胶囊；呼吸光 7s 一轮。
- 优点：情绪表达最强，和「陪伴」定位最贴；层级最简，长期看不累。
- 缺点：去面板化牺牲了信息密度，记忆/任务的可见性下降；对「效率型」使用场景不友好；改动面最大（信息架构呈现层要重排）。
- 实现风险：中-高。细轨 + 弹出层要重组侧栏 DOM；呼吸光需注意 `prefers-reduced-motion`（预览已处理）。

### C · Precision Cockpit（精密任务工作台）
北极星：每条状态都有位置，每个位置都可扫读。顶栏升级为任务状态条（执行中 + 进度 + 等你确认），左栏加任务清单，右栏角色锚点 + 运行指标 + 动态日志；等宽数字、8px 网格、金色只给「需要你看」的事。
- 优点：任务反馈最清晰，正好接上 executive V3 已有的督办/遥测数据；信息分组最利于长期桌面使用。
- 缺点：情绪温度最低，和「安静角落」的文案调性有张力；密度最高，需要对表格化信息做克制才不滑向 SaaS 仪表盘。
- 实现风险：低。全部是现有数据的呈现层重排，不碰渲染层；风险在「别把温度做没了」。

## 4. 推荐：先实现 A，吸收 C 的顶栏

**A 是最值得先做的方向**：它直接回应基线最大的短板（立绘悬浮无空间感），保留全部品牌资产，实现面 80% 是 CSS，唯一渲染层改动（shadow plane）有明确的低成本路径。建议落地时分两步：

1. 第一步（纯 CSS/布局）：`.avatar-stage` 扩为贯通舞台列、脚下投影椭圆加深加实、粒子稀疏化、Holo-Deck 落位舞台侧翼。不动 JS。
2. 第二步（渲染层）：VRM 场景加 shadow plane，rim light 参数按方向 A 调；同时把 C 的「执行中 + 进度 + 等待确认」压缩成顶栏一枚状态摘要（数据源：executive telemetry），不引入 C 的整套密度。

关键细节：投影要「实而暗」（中心 #05080f 60%+，不是金色光斑）；粒子 ≤8 颗且不動；舞台状态文案沿用 `#interactionState`；窄屏舞台必须退为背景层并让出输入框。

## 5. 检查与证据

- 桌面 1440×900 与窄屏 390×844 各截一轮：`web/design-preview/shots/*/page.png`。
- 程序化体检 `check-layout.mjs`（Playwright，项目已有依赖）：横向溢出 / 立绘与标题重叠 / 输入框视口内 / 标题截断 —— 8 项全过（修复过 2 处：A 窄屏立绘压标题、C 的 F1 快捷键标注对比度 4.37→提亮）。
- 截图工具 lint：保留两处「彩色单侧边线」——A 的 Holo 卡左边线沿用生产 `.aqi-block::before` 的既有语言，C 的导航选中边线是仪器式选中态，均服务方向，非模型默认。
- 限制声明：本会话模型无图像输入，截图未做逐像素人眼复核；布局正确性由程序化检查与 CSS 约束保证，观感请以你打开 compare.html 的实际画面为准。

## 6. 文件清单

- `compare.html` 对比页（桌面缩放 / 窄屏原尺寸切换）
- `baseline.html` 基线复刻（直接引用生产 `style.css`）
- `dir-a-midnight.html` / `dir-b-ambient.html` / `dir-c-cockpit.html`
- `check-layout.mjs` 布局体检脚本
- `shots/` 截图证据
