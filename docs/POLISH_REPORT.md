# 既有功能隐含问题与打磨方案报告

**签发：** Role 0（Chief Architect），2026-10-14
**性质：** 只读诊断（未改任何业务代码）。基线 `v0.6-phase2`（commit `0550844`）。
**范围：** 二阶段新特性之外的既有功能——二次元形象与舞台互动、协同工作台、语音副玩法、17 个历史测试红灯。

---

## 一、二次元形象与舞台互动

### 1.1 渲染器与资产现状

- **默认渲染器是 SVG**（`web/avatar/render/svg.js` 的 `SvgRig`），VRM 需在侧栏手动切换。主界面形象下拉只有三项：`svg`（插画）、`vrm`（sample.vrm，状态栏自述"官方测试模型，非最终角色"）、`seed-daily-lids`（日常造型·细腻笑眼，由 app.js 动态补入）。
- **资产闲置**：`web/models/` 下有 6 个 VRM，其中 `seed-daily-hair.vrm`（发丝物理）和 `seed-daily-eyes.vrm`（眼球校准）只在 `avatar_lab.html` 可达，主界面无入口。一阶段大量的眼球/眼睑校准成果（`eye-calibration-v2/v3.json`、seed-lids profile）主要服务于 VRM 路径。
- **发丝物理仅 VRM 有**（`render/vrm.js` 的 springBone 链检测）；SVG 默认形象的头发是静态的，只有 `secondary` 滞后摆动（`avatar.yaml: secondary: 0.15`）——默认形象恰好是灵动感最弱的那个。
- **光影材质**：SVG 有腮红/唇色渐变等 2D 着色；VRM 侧有 face closeup 与 hair depth 处理，但均为实验室调校状态，未做默认参数固化。

### 1.2 驱动器叠加机制

- 架构是**分层仲裁而非裸争抢**：`params.js` 的 `Rigger` 提供 layer + priority + `mute` 封口 + protectedKeys，优先级 `face(5) < idle(10) < emotion(20) < gesture(30) < lip(40)`，`lip` 对 `ParamMouthOpenY` 正确 mute。这部分设计是健康的。
- **但参数重叠面真实存在**：`ParamEyeLOpen/ROpen` 被 5 个驱动书写、嘴型参数 4 个、头部 `ParamAngleX/Y/Z` 3 个；`idle`/`motion`(Motion Magic)/`beat`/`dialogueExpression` 四个来源共写 idle 层且为**累加**，超限仅靠末端 clamp 兜底——高强度演出（节拍 + 程序化动作 + 语篇表情同时进行）存在累加过冲后被硬截断的**抽搐感**风险。
- **与二阶段微句流的不同步是确证缺口**：`chunk.pause_after_ms`/`breath` 一路透传到 `timeline.js`（`withBreathPause` 正确暴露了 `timeline.chunk/breath/breathAt/pauseAfterSec`），但**没有任何消费方**——`playback.js` 严格顺序播放、无段间停顿；rest segment 位于音频时长之外，采样不到；breath 点的轻眨眼/微表情无触发器。现状：微句流只是"更细的切片"，没有"呼吸"。

### 1.3 鼠标/触控微互动

- 已有：mousemove 视线跟随（`gazeMode="follow"`，**默认关**）、click → `look_at_user` 手势 + wake（app.js:1173 已接线）、摄像头面部反应（默认关）。
- 缺失：**触屏**（只有 `mousemove`/`click`，移动端拖拽凝视不可用）；**区域化点击反馈**（摸头/戳脸/戳手无区分，只有一个通用 look_at_user）；**情绪氛围背景联动**（情绪变化不驱动背景色/光/粒子，全场景静态）。

---

## 二、主工作台与副玩法割裂度

### 2.1 页面盘点

- **notes（笔记）**：已集成，"一起写"面板内 iframe（`/static/n?embedded=1`），顶栏一级入口。
- **threads（共同事项）**：已集成，主页面 `companionMount` 区内。
- **editor-insights（观点续聊）**：已集成，写作视图内 iframe + 语音续聊链路。
- **practice（英语练习）**：已集成但**入口过深**——埋在侧栏 `<details>` 偏好里，需展开面板再选三项设置。
- **music（原创音乐）**：**独立整页** `music.html`，侧栏 text-link 新标签打开，用户离开角色。
- **interpret（实时字幕）**：**独立整页** `interpret.html`，同上。
- **voice_taste（语气试音台）**：**孤立无入口**——有"返回阿栖"却没有来的路。
- **avatar_lab（形象实验室）**：独立整页，有入口（探索形象实验室）。
- **card_preview / notepad_preview / blender_study / avatar_preview**：**孤立设计研究页**（演示数据、无后端），无任何入站链接。

割裂评估：听歌与翻译是**完整上下文切换**——用户离开阿栖去操作一个没有角色的页面，这两块应转为主工作台内置视图（或至少保留角色的侧栏/画中画模式）。voice_taste 有真实价值（TTS 语气对比）但不可达。四个设计研究页属于历史资产，建议归档或加显式索引，避免继续稀释 Agent 上下文。

### 2.2 二阶段开关与既有模式的状态机关系

- `speech.enabled`（微句流）：只作用于 `orchestrator.stream_respond`（普通聊天）。体验/练习/助手/主动发言走各自交付器，**固定回复交付器 `deliver_reply` 没有微句**——星图讲述、主动开口因此没有呼吸停顿，与普通聊天体感不一致。双工方向无冲突（barge_in 零迟到音频已有测试）。
- `self_evolution.enabled`（自进化）：**开了也不会动**——前端从不发送 `proactive_check`，也不连接 `/ws/proactive`（详见 R0-2）。即便触发，两个入口的互斥守卫不一致：主 `/ws` 的 `proactive_check` 有 practice/experience 守卫，`/ws/proactive` 的 `check` 分支只有 `not conn.busy`。
- `narrate` vs 各模式：`conn.busy` 门控覆盖播放中/录音中/任务中，practice/experience 会话中同样 busy，**无冲突**；唯一残余风险是 self_topic 经 `conn.begin()` 取消当前轮，若插入时机落在播放刚结束的窗口可能打断 narration 尾音——建议开口前复用 `isIdle` 语义再判一次。

---

## 三、历史测试红灯根因（17 个）

已用 `v0.5-phase1-freeze` 基线 worktree 逐项对照：**17 个全部为既有失败，无一由二阶段引入**。分两类：

### 3.1 Python 7 个——全部是"缺依赖/缺夹具"，零功能退化

- `test_eye_calibration_analysis.py`：`.venv` 未装 Pillow（`ModuleNotFoundError: PIL`）——缺依赖。
- `test_memory_eval.py`：缺 `eval/research/memory_live_cases.json`——缺夹具。
- `test_procedural_memory_boundaries.py`：缺 `eval/research/procedural_memory_boundary_cases_v1.json`——缺夹具。
- `test_procedural_memory_diagnostics.py` / `test_procedural_memory_eval.py` / `test_procedural_memory_induction.py` / `test_procedural_memory_source_projection.py`：同类 `eval/research/` 夹具缺失——缺夹具。

夹具从未入 git（`/eval` 自封盘起被 gitignore，本地生成物被清）。**清零方案**：用各 eval 脚本自带的准备模式（`--prepare-sources` 等，0 次模型调用）重新生成夹具，并固化成 `scripts/` 内可复现命令；Pillow 补进依赖声明，或测试改 `importorskip` 显式降级。

### 3.2 浏览器 10 个——三类根因

- **缺夹具（5 个）**：`test-hand-poses-browser.mjs`（已证实 ENOENT `eval/avatar/hand-poses/before/report.json`）、`test-face-calibration-cache-browser.mjs`、`test-finger-relaxation-browser.mjs`、`test-gesture-calibration-browser.mjs`、`test-gesture-paths-browser.mjs`（均读 `eval/avatar/` 夹具目录）。
- **环境依赖（2 个）**：`test-body-coordination-browser.mjs`、`test-face-shading-browser.mjs`——GPU/d3d11 截图管线，脚本自带 d3d11/webgl 标记，无 GPU 环境不可用。
- **测试过期（2 个）**：`test-companion-browser.mjs`（已证实：`dialogue-review.css` 未进请求白名单即判 unexpected）、`test-note-proposals-browser.mjs`（同类页面级断言：runtime errors / external requests）。
- **待确认（1 个）**：`test-task-card-browser.mjs`——TimeoutError 等待超时，倾向夹具/服务依赖，需补日志定位。

**清零方案**：夹具类跑对应 capture 脚本重新生成（或改为"夹具缺失即 skip + 文档化"，绝不留红）；GPU 类加环境探测，无 GPU 时显式 skip；白名单类把新增资产加进允许清单；task-card 先补日志定位。原则：**宁可显式 skip 并记录原因，也不要让红灯常态化**——常态化的红灯会淹没真实回归。

---

## 四、分角色打磨派单计划

### Role 0（基线清零与主舞台整合）

- **R0-1（P0）测试清零**：Python 7 个（夹具再生成 + PIL 依赖）、浏览器 10 个（按 3.2 三类处置），目标全绿或显式 skip 有记录；把夹具生成固化为 `scripts/prepare_eval_fixtures.py` 一类可复现命令。
- **R0-2（P0）F3 可达性补线**：前端补 presence 循环（连 `/ws/proactive` 或主 `/ws` 定时发 `proactive_check`），统一两个入口的互斥守卫。这是自进化与主动发言从"不可达"到"可用"的关键一线，也是二阶段 F3 事实上的收尾。
- **R0-3（P1）孤立页面处置**：voice_taste 收编入口；四个设计研究页移入归档索引。
- **R0-4（P2）主舞台整合接线**：形象选择器补齐造型（按 Avatar 专项交付的资产清单）；music/interpret 内置视图的 app.js/index.html 挂载（后端路由已就绪）。

### Role 2 / Avatar 专项

- **A-1（P0）微句呼吸落地**：`playback.js` 调度消费 `chunk.pause_after_ms`（段间停顿 + 停顿期间口型放松保持）；breath 点触发一次轻眨眼/微表情（`timeline.breath/breathAt` 已有，补消费方）；固定回复交付器按需支持 micro 模式（与 U-5 联动，需协议小增补）。
- **A-2（P1）叠加整形**：idle 层多驱动（sway/Motion Magic/beat/dialogueExpression）加总限幅与软压缩，消除过冲抽搐；出 before/after 截图与 `test_performance` 回归。
- **A-3（P2）触控与区域互动**：Pointer Events 统一鼠标/触摸（移动端拖拽凝视）；点击热区（头/脸/手）差异化反应；情绪 → 背景氛围联动（情绪表驱动背景色/光，低风险高感知）。
- **A-4（P2）形象资产启用**：`seed-daily-hair` 接入主界面选择器；SVG 默认形象用 secondary motion 近似补发丝灵动；输出各造型对比截图供默认形象决策。

### Role 3（工作台卡片与副玩法）

- **U-1（P1）music/interpret 内置视图化**：从整页跳转变为主工作台视图（原页面保留为宽屏模式），用户不离开角色。
- **U-2（P1）模式状态可视化**：状态栏展示当前生效模式（体验/练习/连续对话/微句流/自进化/星图），解决"用户不知道开着什么"。
- **U-3（P2）practice 入口提升**：从 details 深处提为侧栏一级分段控件。
- **U-4（P2）voice_taste 收编**：语气试音台改为设置内嵌卡片（与 R0-3 协同）。
- **U-5（P2）narrate 与微句流对齐**：与 Role 2 确认 `deliver_reply` 的 micro 增补后，星图讲述同样获得呼吸停顿。

### 优先级总览

- **P0**：R0-1（测试清零）、R0-2（F3 可达性）、A-1（微句呼吸）——一个是信誉债，一个是二阶段事实收尾，一个是二阶段体验收尾。
- **P1**：U-1、U-2、A-2、R0-3。
- **P2**：A-3、A-4、U-3、U-4、U-5、R0-4。

以上条目获批后可整体移入 `docs/TASKS.md` 看板，按既有仪式（红线自检 → 实施 → 集成对齐 → 打标）推进。
