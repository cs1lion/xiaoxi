# 第三轮优化记录（2026-10-18）

本轮目标：把测试全部跑绿，然后继续查前端、3D 人物与说话声音的问题。
每条结论都带实测数字，不把"看起来好了"当成"已验证"。

## 1. 测试基线修复（本轮开始时是红的）

| 问题 | 根因 | 修法 |
|---|---|---|
| `test_executive_v3` 花名册断言失败 | 花名册已从 3 个 Agent 涨到 6 个，断言还写死 `== 3` | 改成"三个内置 Agent 都在"的超集断言 |
| `test_claude_worker_shim` `tool progress is visible` 失败 | Claude shim 把 `tool_use` 发成 `log`，测试期望 `progress` | 改成 `progress`，与 Codex shim 一致 |
| `test_cross_day_companion` 11 失败 + 1 错误（HEAD 上也红） | `should_route` 与 `handle` 对"继续"的定义不一致；`下一步…` 与更正句没有词法门；恢复摘要只取最近 4 条 user_stated，进展洪流把决定挤掉；三方一致性测试读错了 key | 统一谓词、补 `_NEXT_STEP`/`_CORRECTION` 与负例矩阵、`_spoken_records` 保住决定与问题、测试改读 `data['data']['companion_thread']` 并断言非 None |
| `run_evals` offline 层 `fastloop_nonblocking` 失败 | 该 check 现构真 `Orchestrator`，有真 Key 时一次裸 `respond` 约 12s，撞上 10s 死锁兜底 | patch 掉 `complete_raw`，让离线层恢复确定性 |
| 内存 schema 迁移 `near "utility"` 语法错误 | SQL 注释里有一个分号（`outcome signal; utility …`），`_migrate` 按 `;` 切语句 | 先剥掉整行 SQL 注释再切 |
| `ExecutiveService._memory_provider` 少 `@staticmethod` | 收口函数插在它前面，方法被当成实例方法 | 补回 `@staticmethod` |
| `test_voice_turns` 两条 proactive 超时 | `_proactive_check` 调 `_send_proactive_blocks`，隔离环境没编译它，NameError 被 except 吞掉，`turn_started` 永不到达 | 把该函数与两个积木组装器加进隔离 env |

## 2. 说话声音：每块 0.74s 静音被叠加成"一顿一顿"

**实测**（`ffprobe` + `silencedetect`，`stepaudio-2.5-tts`）：

| 指标 | 修剪前 | 修剪后 |
|---|---|---|
| 每块固定开销 | **0.879s** | **0.355s** |
| 有效语速 | 5.2 字/秒 | 6.3 字/秒 |
| 首尾静音 | 前 0.36s + 尾 0.38s | 仅保留 60ms 边缘 |

**根因**：供应商在每个音频块前后各垫约 0.36s / 0.38s 静音。前端
`playback.js` 又会按 `pacing.yaml` 的 `pause_after_ms`（子句 140ms / 句末 320ms）
插入呼吸停顿。两者叠加，逗号后的真实空隙约 880ms、句末约 1060ms——
三块的一段话里有 2~3 秒是死气。

**修法**：`server/speech/tts.py` 新增 `trim_wav_silence()`。开关打开时按 WAV 取流
（`decodeAudioData` 按容器嗅探，交付格式不必与 `tts_format` 一致），修剪首尾近静音，
保留 60ms 边缘避免硬切暴露起收音毛刺。阈值 `TTS_TRIM_THRESHOLD=0.012`、
边缘 `TTS_TRIM_PAD_MS=60`、开关 `TTS_TRIM_SILENCE=true`。

**代价（实测）**：首块合成 3318ms → 3819ms，载荷 74KB → 176KB。首块超前加载
只此一次，子句块并行预合成，所以净效果是**起话只慢约 0.14s，整段反而短约 0.9s**，
且不再一顿一顿。对陪伴角色这是明确的正收益。

**顺带修掉两个同源缺陷**：
- 合成缓存键不含线上格式，`tts_trim_silence` 反转时把另一种格式的字节当缓存返回。
- `taste_api` 把 `format` 报成 `settings.tts_format`，修剪时实际是 WAV，
  试音页会按错误的 MIME 建 Blob。

## 3. 前端：星图手帐（Almanac）落生产

`web/almanac.css`（新）+ `web/index.html` 改结构。三个方向原型在
`web/design-preview/live-20261017/`，截图在 `…/shots/`，选型理由见
`docs/UI_DIRECTIONS_20261017.md`。

- **版式**：Noto Serif SC 600 70px 大标题 + Serif 15px/1.95 正文 + Consolas 9px 元信息；
  纸 `#f2eada`、墨 `#211c16`、朱 `#a93a2a`、金 `#9b7a3e`。圆角全部收到 1~2px，
  去掉" rounded card sameness"。
- **构图**：`34px 竖排书口 | 正文 | 400px 图版` 三栏。建议项变成编号
  一/二/三 的纸面条目，共同事项是带胶带的纸上便签，积木卡是墨色小卡。
- **3D 图版**：`#avatarStage` 收进深色图版（纸托 + 金细线 + 朱印 + 暗角），
  人物从"角落贴纸"变成"被照亮的一张插图"。

**本轮实测抓到并修掉的回归**：移动端与桌面设置面板 `max-height` 过大，
`.settings-section` 拦截 `#sendBtn` 点击，`test-voice-browser` 超时。
现在 1440×1000 / 390×844 / 390×667 / 768×1024 四档面板底沿与 `.composer`
顶沿都留 15~17px。

## 4. 3D 人物：双态镜头确实接到了 lip 时钟

`web/avatar/render/stage.js` 的 `StageCamera` + `config/avatar.yaml` 的
`stage.camera` / `stage.lights.presentation` / `stage.look` / `stage.expression`
上一轮已落地。本轮**实测确认接线正确**，不是死代码：

- `_frame` 每帧读 `this.lip.speaking`，调 `this.svg.setSpeaking(speaking)`
  （`this.svg` 是多态渲染器句柄，VRM 时就是 `VrmRig`）。
- 实测 `stageCamera.mode`：静默 = `idle`（blend 0），开口 = `speaking`（blend 1）。
- 默认表情只用资产真有 morph（`ParamMouthForm` / `ParamBrowLY` / `ParamBrowRY` /
  `ParamEyeLOpen` / `ParamEyeROpen`），`resolveMorphWeights` 按已加载字典过滤。

截图：`eval/almanac-20261017/avatar/{desktop,mobile}-{idle,speaking}.png`。
开口时人物充满图版，脸宽明显超过审计定的 90px 目标；静默时收回中景。

**仍未做（资产级）**：发际线接缝、鬓角缝、嘴部资产。这些要改 VRM 网格，
不在本轮范围内，`docs/AVATAR_VISUAL_AUDIT.md` 的目标保持有效。

## 5. 门禁现状

离线 56 项 + 浏览器 7 项全绿，`scripts/run_gates.py` 一次跑完 **23 道门**，全部
真跑过并断言（详见 §7）。新增门禁：
`test_acceptance_dispatch.py`（52）、`test_workbuddy_worker_shim.py`（34）、
`test_dsh_worker_shim.py`（43）、`test_anthropic_gateway.py`（72）、
`test_tts_trim_silence.py`（11）、`test_voice_taste.py`（2）、
`web/avatar/test_stage_camera.js`（6）、`test_llm_client.py`（31）、
`test_self_evolution_send.py`（10）。

## 6. 需要用户决定的一件事

`127.0.0.1:8000` 上跑的应用进程启动于 2026-10-17 23:54，早于本轮
`server/config.py` 与 `server/speech/tts.py` 的改动。它**已经**在提供新前端
（静态文件按请求读盘），但**没有**语音修剪。要看/听到语音修复，需要重启：

```bash
taskkill //F //PID <占用 8000 的 PID> && .\.venv\Scripts\python.exe scripts\run.py
```

## 7. 测试效果本身（"绿"是不是真的）

前六节修的是产品；本节修的是**判据**。三轮实测里真正危险的不是"测试红"，
而是"测试绿但没验"。做了三件事：

### 7.1 门禁报告：区分"真跑过"与"跳过了"

`scripts/run_gates.py` 一次跑完 23 道门，按退出码 **加输出** 判定 PASS / FAIL / SKIP。
为什么需要：`test-ui-nav-browser.mjs` 在 8000 未起时打印 `SKIP` 并以 0 退出，
`test-body-coordination-browser.mjs` 等缺夹具时同理。逐条看退出码全是绿的，
但那一轮什么都没验。命中 SKIP 即以非零退出（`--allow-skip` 兼容旧口径）。

验证方式：起一个关闭的端口跑 ui_nav 门禁 → 报告 SKIP 且退出码 1；
`--allow-skip` → 0。同一门禁在服务正常时 PASS。

### 7.2 变异验证：每条新测试都必须能抓住它防的那个 bug

不是"写完测试跑绿就收工"，而是把被测行为改坏，确认测试变红。本轮做了 6 次：

| 测试 | 变异 | 结果 |
|---|---|---|
| 情绪三方互检 | 往 `EMOTIONS` 加"幻影" | 退出码 0→1，点名该情绪 |
| LLM 客户端 4xx | 让 4xx 也重试 | 「4xx 只请求一次」失败（3 次） |
| LLM 客户端 finish_reason | 硬编码 `"stop"` | 「截断时 finish_reason=length」失败 |
| 设置面板几何 | `max-height` 改小 140px | 报 `bottom 950 > send top 912` |
| §15 接线 | 删掉 `_turn_context` 里的赋值 | 2 项失败 |
| 自进化发送 | 关掉白名单分支 | 2 项失败 |

其中两次是**第一版测试没抓住**、改写后才抓住的，值得记下来：
- §15 第一版只调辅助函数 `_circadian_and_mood()`，删掉真实接线照样全绿。
  改成走 `_turn_context()` → `_render_turn_context()` 的真实装配路径才有效。
- `config_hot` 第一版直接调 `warn_orphan_overrides()`，删掉 `install()` 里的
  调用照样全绿。改成断言 `install()` 的副作用。

**教训**：测试要钉在"接线存在"，不是"辅助函数正确"。

### 7.3 补上没有测试的核心模块

`scripts/survey_test_coverage.py` 扫出 76 个 server 模块里 7 个完全没有专门测试。
其中 `server/llm.py` 是全项目唯一的模型出口，而它今年踩过的三个坑都不在
"返回了字符串"这一层——新增 31 项契约测试（假 transport，不联网）。

`server/app.py` 的 WS 出口同理：`test_reflect_cycle` 断言存储状态，
没有任何测试看"事件到底发没发"。新增 `test_self_evolution_send.py`，
用的就是这次抓到双重翻译的那条路径。

### 7.4 门禁覆盖的盲区

`check_protocol.py` 的"前端未处理"清单原先只认 `switch` 的 case，把 app.js
放在 if-else 链里的 micro_act / mumble / self_evolution 全误列成未处理。
清单是给人排查用的，**不可靠比没有更糟**——真问题会淹在假阳性里。
补认 `msg.type === "x"` 后从 21 条降到 14 条。

## 8. 遗留问题（本轮发现、未修）

| 项 | 现状 | 为什么不在这轮修 |
|---|---|---|
| 心境没有生产写入方 | `save_mood()` 的调用方只有 `decay_mood()` 自己与测试；`mood_state` 表在生产里始终为空 | 缺的是"什么时候该记一段心境"的产品决策，不是代码。硬造一个写入方会让 §15 从"没接上"变成"接错了" |
| `mood.default_ttl_minutes` 无正确消费者 | 它是"新建心境的默认存活时长"，`decay_mood` 的 `easing_seconds` 是第二段衰减窗口 | 语义不同，硬接上去比不接更糟。等写入方落地一起定 |
| `curiosity_reflection` 不是协议事件 | V2 思想引擎产出它，但协议 §十 没登记 | 发它等于自造 WS 事件。要么改协议，要么让它映射到 `self_evolution` 的 kind——需要 Role 0 决定 |
| `proactive` 事件无人发 | `ambient.build_proactive()` 定义了，但 orchestrator 把 proactive 梯度改写成 mumble（见 §16 注释） | 是协议与实现的有意分歧，不是 bug；删函数或补事件都要先定语义 |
| `seeds.local.yaml` / `response_examples.local.yaml` 不生效 | 已加告警（§config_hot），但覆盖本身仍不生效 | 把两者加进 MANAGED 需要读取方先改走 `*_cfg()`；改协议范围是 Role 0 的事 |
| `task.create` 没有 `due_at` | 「明天下午三点」只能进 description | 扩 schema 要改协议 §25 与前端卡片，不在修 bug 的范围 |

