# 小溪 Web 端优化方案（桌面，基于 OpenDesign 审计）

- 日期：2026-11-14
- **范围：只做 Web 桌面端**（1280×800 / 1440×900 / 1920×1080）。移动端按你的决定不做，`PLAN` 里不再出现移动条目。
- 输入：`AUDIT.md`、`web-1280.png` / `web-1440.png` / `web-1920.png`
- 验收门禁（已存在，可复跑）：`node docs/evidence/20261114-opendesign/verify-ui-budget.mjs`
  **当前基线：13 条失败，exit 1**（`verify-web-budget-baseline.txt`）
- **未改任何产品代码**（`web/*` 有待处理的在飞改动）。

## 0. 先 grill

**前提（不成立就别做）**
1. 1440×900 / 1280×800 是主要使用尺寸——否则 W1 可降级（这是全部方案里唯一的"内容看不见"级问题）。
2. 9–11px 小字是审美选择，不是信息必需——否则"抬字号"会破坏风格，应改为"减信息"。
3. `web/app.js`、`web/index.html`、`web/style.css` 的在飞改动会先落地，方案在干净基线上实施。

**反例（会推翻方案）**
- 若侧栏本来就该"内容多了自己滚"、用户从没看 `side-footer`——那 W1 只需把滚动条做成可见（8px）+ 顶部渐隐，不必砍内容。
- 若 1920 是少数派（内部只看 1440）——W4 可以不做，只保留 `max-width` 一行防御。
- 若形象卡比例漂移是为大屏让位给"一起做"卡片——那锁定比例要先重新分配首屏预算。

**什么时候这套方案会错**
- 只把字号抬到 12px 而不砍侧栏内容：`scrollHeight` 会从 1011 涨到 ~1150，**W1 反而更严重**，必须同时做侧栏收敛。
- 只给 composer 加 `max-width` 不移形象卡：1920 上中间窄、右边空，重心反而更偏。

**要你拍板（不替你决定）**
- **Q1 侧栏怎么解**：A 记忆列表默认折叠到 2 条 +「查看全部」／B 侧栏 300→340px 并砍掉 `.side-intro`／C 分区可折叠（记忆、一起写默认收起）／D 只做"可见滚动条 + 渐隐"不砍内容？
- **Q2 字号**：保留 9–10px 只修对比度，还是统一 12px 起（会更易读，但必须配合 Q1）？
- **Q3 形象卡比例锁到哪**：3:4（0.75，人像感）／4:5（0.80，接近现状 0.81）／16:10（0.625，更"版面"）？高度上限取 `min(68vh, 620px)` 是否可接受？
- **Q4 超宽屏**：内容列限宽 1100px 居中、形象卡仍贴右；还是整页一起居中（形象进内容流）？

## 1. 方案清单（按性价比）

### P0-W1 侧栏不再"静默截断"（1280 少 211px / 1440 少 111px）

- **证据**：`.side` `scrollHeight=1011`，`clientHeight=800/900`；滚动条 1px；`.side-footer` 落在 y≈954。
- **改法（按 Q1 选择）**：
  - 最小改：`.side{scrollbar-width:thin} .side::-webkit-scrollbar{width:8px}` + 顶部 `mask-image: linear-gradient(#000 92%, transparent)` 做"还有内容"的暗示。
  - 结构改（推荐 A）：记忆列表最多渲染 2 条 + 「查看全部 3 条」按钮；`.side-intro` 在高度 <860px 时隐藏（`@media(max-height:860px){.side-intro{display:none}}`）。
  - B：`.app{grid-template-columns:340px minmax(0,1fr)}`，同步减 `plate` 右边距。
- **验收**：门禁 `侧栏内容不被静默截断 slack ≤ 8px` 在 1280/1440 PASS（当前 211/111）；`web-1440.png` 里能看到 `.side-footer`。
- **风险**：砍内容会动到记忆列表的测试选择器（`scripts/test-ui-browser.mjs` 里有基于文本的行定位）。
- **规模**：A 约 30 行（JS 渲染 + CSS）；D 约 6 行。

### P0-W2 微字号收口 + `此刻` 对比度（191 处 <12px）

- **证据**：9px 28 处、9.5px 28 处、11px 68 处、11.5px 44 处；`此刻` 8.5px @ **3.35:1**。
- **改法**：
  1. 新增 `--fs-meta:12px; --fs-micro:11px`（仅用于全大写英文/数字标签），中文标签一律 ≥12px。
  2. `此刻` 前景改 `--gold-deep`（目标 ≥4.5:1）或加 12% 墨色底；顺带把 `.plate-head`（7px）与 `composer-foot .hint`（9px）抬到 11px 以上。
  3. 中文小字行高 ≥1.65，12px 以下不再叠加 `letter-spacing`。
- **验收**：门禁 `<12px 可见文本 ≤ 20` 三档 PASS（当前 99/98/99）；`对比度不达标 = 0` PASS（当前 1 条）。
- **风险**：密度上升→侧栏更长（与 W1 强耦合，**必须同批做**）。
- **规模**：CSS 令牌 + 约 40 处替换。

### P1-W3 形象卡比例锁定（0.81 / 0.70 / 0.57 → 定值）

- **证据**：`plate-inner` 高 471/543/723，宽 381/381/412。
- **改法**（按 Q3）：
  ```css
  .plate-wrap{height:min(64vh,600px);}      /* 不再吃满整列 */
  .plate-inner{aspect-ratio:4/5;height:auto;flex:1;min-height:0}
  ```
  配合 `avatar-stage{inset:0}` 已有规则，VRM 会按固定框取景。
- **验收**：门禁 `形象卡比例 0.66–0.85` 三档 PASS（当前 1920 FAIL 0.57）；三张截图人物取景一致。
- **风险**：1920 上会空出右侧下方，需要配合 W6 做纵向平衡。
- **规模**：2–4 行 CSS。

### P1-W4 宽屏内容上限（1920 输入区 1524px）

- **证据**：composer 884/1044/**1524**；`#textInput` 796px@1440。
- **改法**：
  ```css
  .chat,.composer{max-width:1120px}
  .welcome{max-width:min(560px,52ch)}     /* 已有 546 宽，锁住不随屏宽长 */
  ```
  形象卡继续贴右（`position:absolute;right:…`），不参与限宽。
- **验收**：门禁 `输入区宽度 ≤ 1120px` 三档 PASS（当前 1920 FAIL 1524）。
- **风险**：1920 下左中右三分区重心变化，需要肉眼调一次 padding。
- **规模**：3–6 行 CSS。

### P2-W5 行内小目标（21×19 → ≥28 高）

- **证据**：`停用`/`忘掉` 21×19（6 个）、`刷新`/`清空` 21×23、`memHistory` 12×12。
- **改法**：`.memory-row .mini{min-height:28px;min-width:44px;padding:4px 8px}`，「忘掉」与「停用」之间 `gap:8px`；复选框保持 12px 但把 `<label>` 撑到 28px 高。
- **验收**：门禁 `点击目标 ≥24px` 三档 PASS（当前 5 类失败）。
- **风险**：记忆列表行高变化 → 与 W1 的高度预算联动。
- **规模**：2 条 CSS。

### P2-W6 首屏纵向平衡（空隙 146/161/209px）

- **证据**：`welcome`→`composer` 空隙随屏高增长；形象卡底边固定在 composer 上方 44px。
- **改法**：`.chat` 改 `display:grid;align-content:start`，并给 `.welcome` 加 `margin-block:auto 0` 让留白沉底；或把 composer 改为 `position:sticky;bottom:0`（注意与 `stageFx` 层级）。
- **验收**：门禁新增 `空隙差异 ≤ 40px`（三档 146/161/209 → 目标全部落在 ±40px 内）；截图对比。
- **风险**：`position:sticky` 与语音胶囊、抽屉 z-index 有交互，需要回归 `scripts/test-ui-browser.mjs`。
- **规模**：10–20 行。

### P2-W7 渲染预算（有余量才省，当前不掉帧）

- **证据**：`#stageFx` 1140×900 / 1920×1080 全屏 canvas，8 个常驻动画，5 处 `backdrop-filter`；d3d11 历史 ~59 FPS。
- **改法**：DPR 上限 1.5；`visibilitychange` 暂停 FX；`.plate-inner::before` 的大面 `blur()` 换静态图；`#chat` 超过 50 条消息给旧节点 `content-visibility:auto`。
- **验收**：`node scripts/test-performance-probe-browser.mjs d3d11` 与 `software` 两次同口径，FPS 不降、`avatarFrameCpuWall p95` 不升；后台标签 60s 后 CPU 近 0。
- **规模**：30–80 行。

## 2. 不做 / 不做也不亏

- **移动端**：按你的决定移出范围（相关脚本保留但不进验收）。
- **换框架 / 上 Tailwind**：问题是密度与限宽，不是技术栈；换栈会冲击在飞改动并丢掉手帐质感。
- **给语音快环加动效**：`AGENTS.md` 红线，快环内不得有阻塞调用。
- **重画形象**：W3 只改取景框，不动模型/贴图。

## 3. 执行顺序与门禁

1. **W1 + W2 同批**（两者互相拉扯高度预算，分开做会来回返工）。— **已完成（2026-11-15）**
2. W3（4 行 CSS，收益立竿见影）。— **已完成**
3. W4（3 行 CSS）。— **已完成**
4. W5、W6。— **已完成（2026-11-15，W6 空隙 spread 147/164/171px ≤ 40px）**
5. W7（等前面稳定）。— **已完成（2026-11-15，保守优化）**：`web/app.js` 长聊天列表（>50 条消息）旧节点 `content-visibility:auto` + `contain-intrinsic-size`；`stage-fx` 既有 DPR≤1.5 与 `visibilitychange` 暂停已确认。d3d11/software 性能探针通过（见 `verify-w7-after.txt`）。

每步之后跑同一条命令：

```powershell
npm run test:ui-budget   # 规范入口（scripts/verify-ui-budget.mjs），目标 ALL PASS / exit 0
# 或：node docs/evidence/20261114-opendesign/verify-ui-budget.mjs
```

门禁已提升为 `scripts/verify-ui-budget.mjs` + `npm run test:ui-budget`（2026-11-15），可作为与 `check_protocol.py` / `test_core.py` / `npm test` 同级的桌面 UI 常规门禁（需运行中的 Web 服务，默认 `BASE_URL=http://127.0.0.1:8000`）。后续可在此基础上补充 W6 空隙规则。
