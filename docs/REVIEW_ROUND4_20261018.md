# 第四轮 Review：纠正一处根因误判，补掉三个复发问题

> **⚠️ 过期快照（2026-10-25 回填标注）**：本文为 2026-10-18 的历史记录，其后进展以 `git log` 与门禁命令输出为准；现行交付状态见 `docs/DELIVERY_PLAN_20261024.md`。

日期：2026-10-18
范围：`397a7dc` … `d85838c` 七个提交 + 工作区未提交改动

## 一句话结论

七个修复里六个成立；§12 那个 P0 的**根因判断反了**——不是 `.holo-deck`，是并行改动只加了 `stage-fx.js` 没加 `stage-fx.css`。另外 `397a7dc` 自己重新制造了三个死配置键：它修好了接线，但配置读不进来。

## 一、逐条判定

| 提交 | 声称 | 核实 | 备注 |
|---|---|---|---|
| `397a7dc` | §15 昼夜/心境接进 prompt | **部分成立** | 传参接上了，但 `circadian.timezone` / `mood.*` 三个键读不到 |
| `dd6e26f` | `/api/avatar` 双 handler | 成立 | `check_protocol.py` 覆盖 |
| `033f9ee` | `.local.yaml` 静默失效 | 成立 | 新增点名日志 + 测试 |
| `adad7d7` | 自进化事件双重翻译 | 成立 | 按形状分派正确；`curiosity_reflection` 不发也符合协议现状 |
| `0cd4bca` | seeds 热覆盖 + `due_at` | 成立 | 但同批把 `seed_fork_min/max` 留在 yaml 里，代码从不读 |
| `97300cf` | 积木点击 flake | 成立 | 关动效是对的；但那次超时的真因见第二节 |
| `d85838c` | `--aqi-i` 死接线 | 成立 | 删接线而不是补设计，判断正确 |

### 1.1 `397a7dc` 的漏洞：配置整段被丢掉

`server/memory/evolution.py` 的访问器只放行 `SELF_EVOLUTION_DEFAULTS` 里登记过的键：

```python
cfg.update({key: value for key, value in configured.items() if key in cfg})
```

而 `memory.yaml` 里 `circadian` 与 `mood` 是**嵌套段**，两个键都不在 defaults → 整段丢弃。实测：

```
> self_evolution_cfg()
circadian in cfg? False | mood in cfg? False
```

后果：

- `circadian.timezone` 改了不生效，永远回落硬编码 `Asia/Shanghai`；
- `mood.enabled: false` 关不掉（代码里根本没读这个开关）；
- `mood.default_ttl_minutes` 没有读者。

三个键**当前零症状**，因为回落值恰好等于配置值。这正是 `397a7dc` 提交信息里痛斥的 2026-10-17 那类 bug——同一份 mechanics，隔一天复刻一次。

### 1.2 顺带发现：`seed_fork_min/max` 也是死键

协议 §14 要求"一次成功反思必须派生 1~2 个"，实际闸门是 `loop.py:543` 的模块常量 `REFLECT_MIN/MAX_CHILDREN`，yaml 里那两个键全仓无消费者（`grep seed_fork` 只命中 config 本身）。

## 二、§12 UI P0：根因判断反了

你的判断是 `almanac.css` 的 `.holo-deck{max-height:none}` 由并行工作引入。三条证据说明不是：

1. `git show HEAD:web/almanac.css` 第 250 行就已经是 `max-height:none; overflow:visible`——这条规则本轮没被改过。
2. 本轮新增在 `web/index.html`：未提交 diff 加了一行 `<script src="/static/stage-fx.js">`，但**没有**对应的 `stage-fx.css`。
3. `web/stage-fx.js:48` 会往 `.stage` 里插一张 `<canvas id="stageFx">`；`web/stage-fx.css:11` 给它 `position:absolute;inset:0;pointer-events:none`。表没加载 → 它就是一张流内的默认尺寸画布，`pointer-events` 也是默认的 `auto`。

实测（1440×1000，补表之前）：

```
.stage 第一个子元素（canvas）: y=0, 高 1000
.topbar                      : y=1000  ← 被顶到视口外
.workspace                   : y=1107, 高 18
.composer                    : y=1125, 高 162
#textInput                   : y=1212  ← 已出视口
```

`.stage` 内容总高 1301 > 1000，`overflow:hidden` 把它裁掉，`scrollHeight` 仍是 1301。你量到的"deck 474px 延伸到 y=1489 / summary 压在 input 上"是这个整体错位之后的**下游表现**，不是原因。

补上 `<link href="/static/stage-fx.css">` 之后：

```
.topbar    y=0    h=76
.workspace y=107  h=717
.composer  y=824  h=162   （视口内）
stage scrollHeight = 1000 = clientHeight（无溢出）
```

### 2.1 deck 仍有第二个独立问题（保留限高）

根因修好后 deck 不再被顶到 1000 之外，但它本身仍无高度上限。6 张卡实测：

```
deck    y=271  h=804   （底边 1075，越过 composer 的 824）
#chat   h=0
input   y=911，elementFromPoint 命中 .aqi-block  ← 仍然点不进去
```

所以两个问题**叠加**，只修一个都不够：缺 CSS 让整个 stage 错位；deck 无界让卡片盖住输入框。

修法（`web/almanac.css:255`）：

```css
.holo-deck{ ... flex:0 1 auto;min-height:0;
  max-height:clamp(132px,26vh,300px); overflow-y:auto; overscroll-behavior:contain; }
```

改完 `blocks_browser` 13/13、`companion_browser`、`voice_browser` 全绿。

> 这解释了你之前两次失败：revert `.chat` 到 46vh 没用（chat 不是原因），bound 到 24vh 破 deck 测试（测试假设"所有卡片可见"，与限高天然冲突）。你是在错误的层调试，不是修法不对。

## 三、改动清单

| 文件 | 改动 | 为什么 |
|---|---|---|
| `server/memory/evolution.py` | defaults 登记 `circadian`/`mood`/`seed_fork_min`/`seed_fork_max`；merge 支持一层深合并 | 让 §15 的配置真的读得到；只写 `mood.enabled` 时不再丢掉 `default_ttl_minutes` |
| `server/dialogue/orchestrator.py` | `_circadian_and_mood` 读一次 cfg，接 `mood.enabled` 阀门 | 配置关掉真的关掉 |
| `server/assistant/loop.py` | `validate_reflection_payload(..., min_children=, max_children=)`；新增 `_fork_bounds`；`_reflect` 传参 | `seed_fork_*` 接线，默认行为不变 |
| `scripts/run_gates.py` | 新增 `config_keys` 门禁 | 审计脚本写进仓库却不跑，等于第三次死接线 |
| `scripts/audit_config_keys.py` | 跨文件 consumers / 收参数变量 / 按作用域扫 | 见第四节 |
| `web/index.html` | 补 `stage-fx.css` | P0 根因 |
| `web/almanac.css` | deck 限高 + 内部滚动 | 第二个 P0 |
| `scripts/test-companion-browser.mjs` | 夹具补 `stage-fx.css` | 夹具清单漏了 index 新引用的样式 |
| `scripts/test_core.py` | +8 项断言（56 → 64） | 配置可读性、mood 阀门、`seed_fork` 接线 |

## 四、`audit_config_keys.py` 本身不能信

写完之后第一次运行就是红的（exit 1），报 4 个死键：`circadian`、`mood`、`seed_fork_min`、`seed_fork_max`。也就是说：**它本来能抓到 §15 的问题，只是没人跑**。

更麻烦的是它有三处结构缺陷，即使跑了也报不全：

1. `consumers` 被假定与访问器同模块，而 `_reflect` 在 `server/assistant/loop.py` → 代码侧检查整个是空转，"读到的键"只剩访问器自己那一行。
2. 只收局部变量，不收函数参数 → `cfg` 作为参数传入的消费函数全部漏掉。
3. `read_keys` 按变量名在**整棵树**上匹配，不看作用域。修完跨文件后立刻出现假阳性：`loop.py` 里别的函数的 `cfg.get("mumble_seconds")` 被算到 `self_evolution_cfg()` 头上，一口气报出 12 个不存在的死键。

修完三处后，同一份配置的"读到的键"从 1 个变成 11 个，检查才真正开始工作。这是这轮最值得记的一条：**一个只会漏报的检查脚本，比没有检查更安全；一个既漏报又假阳性的脚本，会让人放弃使用它**。

## 五、待决三项

### 5.1 mood writer：同意你的判断，但可以给一个有界条件

你说的对：`mood_state` 空不是因为忘了写，而是"何时写"是产品判断。每轮反思都写会毁掉 §15.1 的惯性。

建议的写入条件（三条同时满足）：

1. 只由 **idle 反思**写入（不在对话轮次里写，避免"用户刚说一句就改心境"）；
2. 当前**无** active mood（`state in (None, "resolved")`）时必写；有 active mood 时，仅当新 `discovery` 与现 `narrative` 的词汇重叠 **< 0.3** 才覆盖；
3. `expires_at = now + mood.default_ttl_minutes * 60`（这个键现在能读到了）。

这样：低新颖不覆盖 → 惯性保住；高新颖能转向 → 心境不是化石；表里有数据 → §15.1 的衰减链（active → easing → resolved）第一次真正跑起来。

需要你拍的只有一个数：0.3 这个重叠阈值。代码照上表就能写。

### 5.2 `curiosity_reflection`：协议没登记，不发是对的，但形态暧昧

`docs/PROTOCOL.md` §10 只登记了 `self_evolution`。`loop.py:995` 却构造了带 `child_seed_ids` / `reflection_id` 的完整事件，然后 `app.py` 静默丢掉。这是最容易被下一个人误判成 bug 的形态——payload 造得这么完整，摆明是给谁用的。

两条路，选一条：

- **要消费**：登记进 §10，前端接（星图/模式条都能用 `child_seed_ids`）；
- **不消费**：改名 `_curiosity_reflection`（与 `_audio_done`/`_llm_done` 同约定），并删掉没人读的 payload 字段。

维持现状的代价是每次 review 都要重新判一遍。

### 5.3 §16.2 的矛盾：补一句话就够

冲突在两句之间：

- `mumble` 的 `voice:true` 是**有声**主动开口，"不要求用户作答"；
- §十（`PROTOCOL.md:188`）写"`require_authorization` 为 true 且用户未授权时，**禁止任何主动开口**"。

`mumble` 算不算"开口"？§16.2 只说"授权门复用"，没把 `voice` 和授权门绑起来。

建议补丁：`require_authorization=false 时 mumble 只出气泡、`voice` 强制 false；micro_act 无声，不受此门约束。` 一句就能让两处自洽，且不改任何字段。

## 六、§10 视觉层：核实成立，同意不发明设计

`grep` 全部 `web/**/*.css`：

| 类 | JS 里加了 | CSS 消费 |
|---|---|---|
| `nav-spotlight` | `executive.js:391` | 无 |
| `is-highlighted` | `executive.js:167` | 无 |
| `editor-discussion-message` | `app.js:289` | 无 |
| `--aqi-i` | 已删（`d85838c`） | 无 |

不发明设计是对的。但建议再走一步：把这三处登记进 `docs/UI_VISUAL_AUDIT.md` 的待实现清单，并把 `executive.js:391` 的注释从"点亮 .nav-spotlight 动效"改成"类名已加、样式未实现（见清单）"。理由和 `d85838c` 完全相同：**注释里承诺一个不存在的东西，下一个人会按注释去找**。

## 七、验证

```
门禁：29/29 全绿（含新增 config_keys）
test_core：64 passed
config_keys：问题键 0，exit 0
```

变异验证（4 项，全部被抓，恢复后 64 绿）：

| 破坏点 | 预期 | 结果 |
|---|---|---|
| defaults 删掉 `circadian`/`mood` | 配置读不到 | FAIL ×2 |
| `mood.enabled` 判断去掉 | 关不掉心境 | FAIL |
| `_reflect` 不再传 `min_children/max_children` | 闸门失效 | FAIL |
| （反向）恢复全部 | 全绿 | 64 passed |

## 八、给下一轮的提醒

1. 这个项目里"注释/commit 声称有效果"与"真的有效果"之间已经三次脱节（`--aqi-i`、§15、`stage-fx.css`）。凡是新增资源引用，先问一句：**谁消费它**。
2. 门禁没进去的检查脚本，等于没写。`config_keys` 已经进 `run_gates`。
3. 遇到"某个元素挡住点击"，先量 `.stage` 的第一个子元素和 `topbar` 的 y——错位布局的症状总是长在错位区域的下游。
