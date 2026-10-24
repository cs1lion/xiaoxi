# voice-lab — 语音前端探索区（与主线隔离）

**性质：** 独立探索区，不是产品代码，不是路线图承诺。
**设立日期：** 2026-10-24。
**为什么存在：** 探索端到端实时语音（StepAudio 3 Realtime / Chat）会不会改变现有级联管线（ASR→LLM→TTS + 快环/慢环）的判断，而这份探索**不应影响 [DELIVERY_PLAN_20261024.md](../docs/DELIVERY_PLAN_20261024.md) 的 Phase 0–4**。

## 隔离是怎么做到的（四条，缺一条就漏）

1. **零 import，单向。** `voice-lab/` 不 import `server/`、`web/`、`config/` 任何东西；反之也不许有人 import 它。两边共用的是**问题**，不是代码。要参考现有实现就复制一份进来改，复制品不回写。
2. **不进门禁。** 这里的脚本一律不加进 `run_gates.py`、`package.json` 的 test 脚本、`scripts/check_protocol.py`。黄金命令的输出里没有这里，主线绿不绿与它无关。
3. **独立凭据与额度。** 用自己的 `.env`（已 gitignore），不复用订阅/主 `LLM_API_KEY`。沿用仓库既有规矩：付费音频能力必须显式 opt-in（见 [server/config.py](../server/config.py) 中 `asr_stream_url` 那条注释）。每次跑实测要写死请求上限，超出即停。
   > **2026-10-24 实测注（已于同日结清）**：写下这条时，voice-lab 忽略规则只存在于主工作区**未提交**的工作副本，`HEAD` 的 `.gitignore` 并没有它们——「已 gitignore」在入库层面当时是虚的（正是 AGENTS.md 待办第 1 条教训的同一形状）。**该缺口随后由提交 `56ce458` 自动补上**（三条规则现已随 `HEAD` 入库，`voice-lab/.env`、`/`.venv/`、`/outputs/` 实测被正确忽略）。本条保留为时间线记录，不代表当前状态。
   > 同日例外备案：主人在 preview 限免期间**明示授权**把主 `LLM_API_KEY` 复制进 `voice-lab/.env` 复跑（探针代码仍只读该文件，不读根 `.env`）。这是人对自家额度的决定，不是对本条的静默绕过。
4. **分支独立，合入是刻意动作。** 探索在 `research/voice-frontend` 上做，不 rebase 到主线、不带进 Phase 票据。想让任何东西进生产链路，必须走下面的出口流程。

## 只有两种出口

| 出口 | 形态 | 落点 |
|---|---|---|
| **采纳** | 一份 ADR：为什么级联 / 什么条件下放弃 / 本次实测数据。ADR 立项前须按 [AGENTS.md](../AGENTS.md) 的红线口径先问。 | `docs/ADR_*.md`，此后才允许排进交付计划 |
| **停泊** | 一段「不做的理由 + 证据」，写回本文件末尾的「结论」区 | 本文件；不进 `docs/` |

**没有第三种出口。** 任何「先顺手接上试试」都算绕过 ADR，是把探索污染进主线的方式。

## 本区与 docs/ 的关系

`docs/` 是规范语料（协议、验收、交付计划）。把端到端语音的调研放进 `docs/`，等于对外宣称它进了路线图 —— 这正是本区要避免的暗示。所以调研产物落在 `voice-lab/research/`，只有升格为 ADR 时才进 `docs/`。

## 目录约定

- `research/` — 一手文档核实记录（带出处，文档没写的标「文档未说明」）
- `probe/` — 一次性实测脚本：连 WS、喂录音、看事件。写完即可扔，允许脏
- `outputs/` — 跑测原始输出（gitignore，不入库）

## 已知前提（探索别浪费在这里）

现有级联管线不是技术债，是**已拍板的产品选择**：上下文由客户端逐轮装配，才能做到记忆三档置信度、检索为空注入负向约束、SQLite 物理硬删除不留索引；每轮 `[情绪]` 标签驱动视觉层。探索的靶心是**话轮与打断的所有权**（[docs/FULL_DUPLEX.md](../docs/FULL_DUPLEX.md) 承认无语义附和识别、[docs/archive/DUPLEX_STRESS_RESULTS.md](../docs/archive/DUPLEX_STRESS_RESULTS.md) 证伪了能量 VAD + 线性回声相关），不是「要不要换掉 LLM」。

## 结论

**2026-10-24 ｜ 出口＝停泊（不做的理由）。HEAD 锚 `00ecc4b`。**
数据出处：一手核实 `research/steaudio3-realtime.md`（§12/§13/§15、障碍清单 B1/B2/B3/B7，含文档原文与 llms-full.txt 全文计数）；探针 `probe/realtime_probe.py --selftest` 离线全绿（8/8）；两道闸实跑行为（无 `--live`→exit 3；`--live` 无凭据→exit 4，绝不回退主 `LLM_API_KEY`）。

**为什么是「不做」而不是「ADR 候选」**：本区出口表规定「文本注入发声不支持 → 本方向基本出局 → 直接写不做的理由」。这一触发条件在文档协议层已成立（下述 Q2），且 Q1/Q3 缺的恰是形象层/话轮层必须「可观测」的信号。三项同向，不构成采纳。

**真实音频实测状态**：上午一轮为「仅文档证据」（`voice-lab/.env` 不存在、主凭据按隔离规矩 3 未复用）；**同日下午主人口头授权复跑**（preview 限免 + 「用老 key+模型名直接调用」），实测已执行，结果见下方「同日补」一节——其中 Q2 的判定被实跑**部分改判**（软路径可用但无保证），以「同日补」为准。

---

### 同日补：真实音频实测已执行（2026-10-24 下午，HEAD `4382eb4` 后修订版探针）

主人拍板：preview 限时免费、直接用主 key 复跑坐实（明示授权复用，已记入本区；探针仍只读 `voice-lab/.env`，不读根 `.env`）。
**用量报账**：约 9 个 WS 会话（含 1 次零音频握手矩阵、2 次传输异常中止），累计合成语音 ≈ 155 s；模型 `stepaudio-3-realtime-preview` 限免，`/v1/realtime` 走不带 plan 的 URL（`step_plan` 路由对该模型 404，实测坐实 research §1；对非 preview 的 `stepaudio-2.5-realtime` 返回 **402**，免费只限 preview）。原始事件全部在 `outputs/`（gitignore）：`text-injection-197346f4.json`、`backchannel-1a4ddc0a.json`、`backchannel-6aaa3128.json`、`alignment-8d1467e9.json`、`smoke-handshake.json`。录音为 Windows zh-CN TTS 合成（非真实麦克风原音，仓库零原音）。

**实跑改判与坐实清单**（只动「文档未说明」项，设计判定不变，出口仍是停泊）：

- **Q2 改判「有软路径，但无硬路径」**：
  - 硬路径（`conversation.item.create` 注入 assistant 文本 + `response.create`）实测**静默零音频**（`status=completed` 但 `audio_deltas=0`、transcript 空）——research B1「无法填充 Assistant 音频消息」被坐实，且是**不报错的死法**，比文档更糟。
  - 软路径（开场白技巧：`instructions` 要求原样复述 + user 种子触发）实测**逐字节一致地播出**目标句（transcript 4960ms 音频）。
  - 结论修正：不是「完全做不到」，是「只有 prompt 级软保证」。对 `deliver_reply` 的逐字一致硬要求，单发成功≠保证（文档自认不保证遵循；且空历史裸触发实测 400 `continue_final_message` 错误——软路径本身时序也脆）。**出局判定维持，理由从「不支持」改为「只有不可保证的软支持」。**
- **Q1 坐实「协议层不可观测」，并补行为数据**：
  - 两轮实跑一致：`speech_started` 字段全集 = `{audio_start_ms, client_start_ms, event_id, item_id, type}`，**无分类字段**。
  - 行为面（助手正在长独白出声时喂入六条 TTS）：`speech_started` 检测延迟首段干净值 **≈469 ms**（run A「是是是」）；run B 的 1.6s 值是被批量归段污染的（段起点在我方窗口之前）。期间助手音频**连续流不断**（speech_started 之后仍收到 246 个 `audio.delta`）——**服务端不因打断而自动停发/压低**，坐实 research §15「文档未说明自动停发」=实测不发生。
  - 行为层**未能逐条比较（方法受限于 VAD 成段）**：六条 TTS 顺序喂入 + 助手长独白，整场只出了 **1 个 `speech_started` + 1 个 `speech_stopped` + 1 条转写「对」**——只有第一条（附和「对」）被服务端单独成段识别为话语，其余五条（含三条打断「等等/停一下/别说了」）**根本没被逐条识别成独立话语**。因此**无法**在同一会话里对比「打断条 vs 附和条 服务端反应是否不同」——这是喂法/成段的局限，不能算「实测证明二者行为一致」。
  - 但**确凿的负面信号有两条**：(1) 无论成没成段，`speech_started` **两轮字段恒为** `{audio_start_ms, client_start_ms, event_id, item_id, type}`，无任何分类/意图/置信字段；(2) 助手音频在识别到用户「打断」的整个窗口内**连续流不断**（run A：speech_started 后仍收 246 个 audio.delta；run B：586 个），**服务端不因用户开口而自动停播或压低**。即：协议层无分类信号 + 行为层无自动让话，两条都指向「快环拿不到可据此决策的打断信号」。
  - **research 两处需修正**：realtime 侧实测有 `conversation.item.input_audio_transcription.delta`（单轮 21 个）与 `.completed`（「对」字级回填）——§17 表中「无 `.delta`、只有 completed」不准。这给了我方一条**自建语义门**的路（拿服务端用户转写自己判附和），但延迟在收句后（+≈1.8 s），救不了快环。
- **Q3 坐实「两项皆缺」，外加三个实测新事实**：
  - 字级时间戳：全事件树扫描 0 命中（含 `audio_transcript.done`/`audio.done`/`done`/`output_item.done`），B3 坐实。
  - 服务端**从不主动报**「播到第 N ms」：cancel 后事件链 `audio.done→content_part.done→output_item.done→done` 均无偏移字段，`status_details=null`。
  - **`conversation.item.truncate` 实测被服务端接受**并回 `conversation.item.truncated`（文档 0 次出现但实现存在——§11 偏离表第 3 条可升级：从「SDK 有文档无」改为「SDK 有、文档无、**服务端实现**」）。但它只是**把我方自算的 `audio_end_ms=2000` 原样回显**，零信息增量，救不了「用户实际听到哪里」。
  - 打断卫生差：`response.cancel` 之后仍收到 8 个在途 `audio.delta` 才见 `audio.done`（须客户端自清缓冲，坐实 demo 靠 `wavStreamPlayer.interrupt()` 的原因）；且两轮实测中**服务端在 cancel/truncate 时段直接掐死 TCP**（无 close frame），生产接入必须自带重连与幂等。
- **VAD 默认开关之争（B5）**：`session.update` 显式配 `turn_detection` 后行为一致，无需裁决；但 `silence_duration_ms=400` 下连续快喂六条会**闩锁成一条长话语**（只出 1 个 `speech_started`、`audio_start_ms` 恒 0），条间留 ≥1.5 s 才逐条成段——工程约束，比文档矛盾更要紧。

**改判后净结论（不变）**：软路径能逐字播出一次，但快环的三样硬需求——打断可观测、实际播放位置回执、字级对齐——实测全缺，外加取消时序脏（在途块+掐连接）。把话轮所有权交给一个**不告诉你它怎么判、判了也不停、停了也不说停在哪**的服务端，正是 `FULL_DUPLEX.md` 要解决的病换个宿主。**停泊维持。**

### Q2 文本注入发声 — 硬路径不支持（本方向出局触发项）〔实跑改判见下方补记〕
- 无任何「客户端给文本 → 服务端逐字念」的接口字段。`conversation.item.create` 文档原文「**无法填充 Assistant 音频消息**」；`role=assistant` 注入只到「支持文本内容的消息项」，未说会被朗读。
- 最接近的是把 `instructions` 改成「请原样无修改地输出下面这段话」，而**同一文档声明 instructions「不能保证模型会遵循」**。
- 本项目 `deliver_reply` 固定交付器（PROTOCOL narrate/proactive/mumble/self_topic 全走它）要求**文本与语音逐字一致**，软约束满足不了硬要求 → 判定：**不支持可靠逐字播报**，触发「出局」。

### Q1 附和 vs 真打断 — 协议层不可观测
- 能力只在**模型内部**（技术报告：320ms 块 + 状态 token 判「继续听/让出话权」；AA 子集 Backchannel 自评 98.0），但 **API 层零字段**：`input_audio_buffer.speech_started` 只有 `audio_start_ms`/`item_id`，不携带任何「这是打断还是附和」的分类或置信度（B7）。
- 现有靶心正是这条：`docs/FULL_DUPLEX.md` 自认「短促『停』也可能被忽略」，`DUPLEX_STRESS_RESULTS.md` 证伪能量 VAD + 线性回声相关。realtime 把判定收进服务端却不告诉我方结论 → 快环拿不到可据此 duck/cancel 的信号，等于没解决所有权归属，只是把黑盒换了地方。**（此项可实跑坐实，但文档已足以判「不可观测」。）**

### Q3 截断偏移 + text↔audio 对齐 — 两项皆缺
- **截断偏移**：服务端不告知「上一段实际播到第 N ms」。`response.cancel→response.cancelled`、`.audio.done`、`response.done` 均不带偏移；`conversation.item.truncate/.truncated` 在阶跃**全部文档出现 0 次**，官方 demo 的偏移是**客户端自算**（本地播放进度 ÷24000），非服务端契约（B2）。
- **字级时间戳**：realtime 输出侧 `words`/`timestamp`/「时间戳」计数 0（B3）。形象层 `PROTOCOL.md §二/§八` 硬依赖 `audio.text`（丢了口型直接死）与 `audio.word_timings`（缺则只能 `alignment:"estimated"`）；`§十一` 打断契约与「续播被打断句」需要「用户听到哪里」，realtime 侧给不出。两项缺一即接不进，此处两项全缺。

### 未决的实跑项 →〔同日已全部复跑，见「同日补」〕
- VAD 默认开/关（API 参考 vs 开发指南自相矛盾，B5）：实跑显式配置，未裁决默认值；但发现快喂闩锁问题（比默认开关更要紧，见同日补）。
- 采样率 24k：实跑按 24k 喂 pcm16 全链无异常（audio/delta 播放时长与 transcript 比例吻合）；仍属 demo 工程证据，无契约文档。
- 音色：`linjiajiejie` 实连可用；`stepaudio-3-realtime-preview` 官方音色清单仍未公布。

**停泊理由（一句话）**：阶跃把 Realtime 设计成「自带大脑的完整闭环」（服务端持历史、自推理、自发声、判定不外露），而本项目要求快环「只搬运、判断与记忆装配在我方手里、文本语音逐字一致、话轮可观测」。三个靶心问题在协议层要么不支持、要么不可观测；接入的前提是接受它替本项目做决策，与已拍板的级联产品选择正面冲突。**本区到此停泊，不立 ADR、不排进交付计划、不接进任何生产链路。**
