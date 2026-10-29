# 小溪 (Xiaoxi)

**个人中文语音 Agent：台前语音快环（System 1）+ 幕后 Agent 慢环（System 2）。**
对着她说一句就能派活，后台的 Agent 自己规划、自己跑、干完回来告诉你；她记得你，但不会为了显得亲近而编造没发生过的事。

要解决的三件事：

- 复杂工具调用**堵住语音流**
- 长期记忆检索**诱发幻觉编造**
- Agent 干活过程**不可见**——只有一张表，没有人在那儿忙

## ✨ 四个核心

**⚡ 快慢双环解耦**

- **台前快环**：流式 ASR → 对话管理 → 分级记忆检索 → 流式生成 → 情感 TTS。快环里不出现阻塞调用，识别到复杂委托立即派单、异步卸载到慢环。
- 委托确认走卡片显式点击（`docs/PROTOCOL.md` §七：`assistant_confirm` 单次消费；拒绝也回一个有声结果，工具真失败时按真实回执展示）。
- **幕后慢环**：orchestrator-workers，任务规划 → Worker 异步执行 → 遥测观测。同一步骤签名连续重复 3 次判「鬼打墙」熔断（`server/assistant/telemetry.py`，阈值见 `config/agents_roster.yaml` 的 `spin_guard`）。
- **自进化护栏**：L1 查真实回执、L2 查已登记判例，两层都是确定性的，同一次交差永远得到同一个结论。老板驳回并留下反馈后，从反馈里归纳新判例注入后续 Prompt；打回上限 2 次（`server/assistant/evaluator.py` 的 `MAX_RETRY`），第 3 次仍不过转 BLOCKED 请示。

**🧠 置信度分级记忆**

- 检索结果按三档确定性装配进 System Prompt：【你明确说过的】可以直接当事实、【我的推测】必须留余地、【我不确定的】先求证——从 Prompt 层约束幻觉，不指望模型自觉。
- 检索为空时显式注入负向约束（`server/persona.py` 的 `EMPTY_MEMORY_CONSTRAINT`），禁止用预训练常识脑补共同经历；删除是 SQLite 物理硬删除，索引从表实时重建，没有「删了但索引还在」的记忆幽灵。
- 记忆四类（`fact` / `episode` / `todo` / `self_state`）之外还有一层程序性经验（`lessons`）：自我演化周期把经历蒸馏成「什么情况下该怎么做」的短条，只在触发词命中时注入，且装在明确的不可信数据边界里；有害次数够了就退役（`server/memory/lessons.py`）。
- 检索分两段：先用便宜的 `RankingRow` 排序，只对存活的 top_k 取完整记录（见下文「工程实测」第 5 条）。

**🔌 可插拔与心流防打扰**

- 花名册 `config/agents_roster.yaml` 加一行就多一个 Worker；内置适配器五种：`manual` / `cli_subproc` / `http_api` / `mcp` / `durable`（以文件里的 `adapter_types` 为准），调度核心零改动。
- `persona` / `memory` / `pacing` / `avatar` / `agents_roster` / `seeds` 支持 `.local.yaml` 覆盖与热重载（`server/config_hot.py`），改行为不用改代码。
- 长任务可交给本机 pi-durable sidecar（`agent-runtime/`，Node 常驻）：每步 checkpoint、进程死了能续跑。默认关闭，`XIAOXI_DURABLE_ENABLED=1` 点亮。
- 你正在打字或写作时，后台来的卡片不弹，只点亮战况胶囊报数；停手过了静默窗再按原序补弹，内容不改、不丢（协议 §23.6，`web/aqi-flow.js` 的 `isQuiet`）。

**🏘️ 像素世界**

![本仓 `/` 的实况（2026-10-28 截图）：小镇与下方状态行读同一份台账快照](docs/assets/world-live.png)

- `/` 打开就是这座小镇，`/chat` 是旧聊天页；世界的构建产物不在时，`/` 回一张写着构建命令的说明页，而不是 5xx。
- 世界是**纯投影**：只读 REST + WS `commission_update` 增量，不写台账、不 accept/submit。渲染换掉不影响逻辑，逻辑换掉不影响渲染。
- **不撒谎**是硬约束：没发生的事不写进日报，没有真实数据就不摆卡片，工具真失败时显示失败；台账快照取不全时状态行会明说「台账可能未取全」，不让「少了一条」看起来像「被撤单了」。
- 视觉有预算：24 色世界 + 8 色 UI（`docs/ADR_PALETTE_24.md`），越界取色由 `pixel-world/scripts/audit-palette.mjs` 挡下（本地 strict、CI 另有 palette-strict 工作流）。
- **2026-10-25 已分家**：渲染与模拟内核迁往独立仓 [pixel-world-engine](https://github.com/cs1lion/pixel-world-engine)（源提交 `05db039`）。本仓 `pixel-world/` 全量保留但不再演进，只作历史契约查证（`pixel-world/MIGRATED.md`）；要改世界请去新仓。两仓之间「世界读什么」的接缝契约仍是草案（`docs/ADR_DRAFT_WORLD_SEAM.md`，待拍板），所以本仓冻结产物仍按台账投影，而新仓已改读自己的事件日志。

## 🏗️ 系统架构

```text
        config/*.yaml   角色契约 · 记忆策略 · 节奏 · Worker 花名册（热重载）
              │
              ▼
┌───────────────────────────────────────────────────────────┐
│ 台前快环 (System 1) · 实时语音                            │
│                                                           │
│   麦克风 → VAD 断句 → 流式 ASR                            │
│                 ↓                                         │
│   对话管理 + 分级记忆检索 (SQLite 字符 n-gram TF-IDF)     │
│     ├─ 日常对话 → 流式生成 → 情感 TTS → 立绘              │
│     └─ 复杂委托 → 卡片确认 → 异步卸载至慢环               │
└───────────────────────────────────────────────────────────┘
                       ▼
┌───────────────────────────────────────────────────────────┐
│ 幕后慢环 (System 2) · Orchestrator-Workers                │
│                                                           │
│   任务规划 → Worker 异步执行                              │
│     (manual / cli_subproc / http_api / mcp / durable)     │
│     ↑                                                     │
│   遥测监控：步骤空转 / 连续异常 → 熔断                    │
│     ↓                                                     │
│   L1/L2 评估器：驳回反馈 → 归纳新判例                     │
└───────────────────────────────────────────────────────────┘
                       ▼
┌───────────────────────────────────────────────────────────┐
│ 多模态呈现层                                              │
│   情绪标签 → 可控音色 TTS + 口型 / 表情 / 视线            │
│   打字/写作时只延迟不丢弃 → 战况胶囊 / 任务抽屉 / 星图    │
└───────────────────────────────────────────────────────────┘
                       │
                       ▼  只读投影（不写台账，不 accept/submit）
┌───────────────────────────────────────────────────────────┐
│ 像素世界 · `/`（构建产物挂在 `/world`）                   │
│   REST 全量 + WS commission_update 增量                   │
│   台账状态 → 她起身 / 进屋 / 开工 / 交付                  │
└───────────────────────────────────────────────────────────┘
```

性格不是一句人设，而是四层契约（身份 / 稳定倾向 / 互动方式 / 可变状态），每条带正例、反例与边界，另有一层「知识」写立场与好恶；并写死一条优先级：**性格与事实准确性冲突时，准确性优先**（`config/persona.yaml`）。

## 🚀 快速开始

```powershell
uv venv
uv pip install -r requirements.txt
Copy-Item .env.example .env                       # 可选：填 LLM_API_KEY 接入真实模型
npm install --ignore-scripts                      # 前端单测依赖（跳过 Playwright 浏览器下载；只有浏览器门禁才需要浏览器）
Push-Location pixel-world; npm install; npm run build:world; Pop-Location
.\.venv\Scripts\python.exe scripts\run.py         # 打开 http://127.0.0.1:8000
```

`pixel-world/dist` 被 `.gitignore` 排除，**新克隆里没有世界产物**——不跑 `npm run build:world`，`/` 只会显示一张写着这条命令的说明页。构建后再启动服务，产物才会挂到 `/world`。

**没有 API Key 也能跑通多少**：LLM 自动进离线 Mock（`server/llm.py` 的 `offline`），文字对话、记忆读写、任务调度、前端交互照常。**但语音不会自动降级**——`TTS_ENGINE` / `ASR_ENGINE` 各自要自己的凭据，没有就是明确失败（`stepaudio` 无凭据 → TTS 轮次以 `reply_tts_failed` 收尾，ASR 直接抛错），不会假装播过。想在无凭据下体验完整轮次，把 `TTS_ENGINE` 设成 `silent`：文字与事件流照常，轮次里没有 audio 帧。

```powershell
.\.venv\Scripts\python.exe scripts\check_protocol.py   # 协议漂移 + REST 接缝
.\.venv\Scripts\python.exe scripts\test_core.py        # 离线核心基线（不需要网络 / API Key）
npm test                                               # 前端单测（无浏览器依赖）
.\.venv\Scripts\python.exe scripts\run_evals.py        # 回归评测离线层
.\.venv\Scripts\python.exe scripts\run_gates.py        # 门禁总表：跳过即失败，道数以输出为准
```

条数一律以实跑输出为准，本文不写死。完整命令、门禁裁决口径与 live 评测见 [docs/TESTING.md](docs/TESTING.md)。

## 🧪 工程实测

**1. 模型选型：为什么弃用 `stepaudio-2.5-chat`，锁定 `step-3.7-flash`**
选型期用三条诱导提问探针（询问从未发生过的共同经历）对比：`stepaudio-2.5-chat` 即便 System Prompt 写明「你没有任何相关记忆」，Audio-Chat 微调出的「接话顺从」倾向仍会顺口编造细节；`step-3.7-flash` 每次都能承认「无相关记录」。这组探针现已固化成可复跑的用例 `evals/cases/hallu-empty-retrieval-live.yaml`（正则 + 异源裁判双判，判不出结论记 review，不假装判过）。性格因此改由 TTS 的 `instruction`（自然语言情绪指令）承载：音色固定，情绪可变。

**2. 空检索必须显式声明，不能只传空列表**
消融实验发现：只传空记忆列表时，LLM 仍有很高概率依赖常识脑补。工程上必须在 Prompt 注入显式空状态——未检索到就直接向用户求证，严禁顺从提问编造事实。这条有离线用例守着（`evals/cases/hallu-empty-retrieval-offline.yaml`）。

**3. Few-Shot 示例必须每轮都带情绪标签**
下游 TTS 音色切换与 Avatar 表情状态机依赖句首 `[情绪]` 标签。仅靠指令约束时，多轮长对话后会出现指令遗忘；必须让示例集中每一轮 Assistant 回复都携带合法标签，用 In-Context Learning 维持长程格式对齐（`config/response_examples.json` + `evals/cases/fewshot-emotion-tag-offline.yaml`）。

**4. 语音快环的基线数字（带锚点）**
Mock 口径基线见 `docs/LATENCY_BASELINE.md`，锚点 HEAD `94b1abe`、2026-10-24（Windows 11 本机回环）：首音延迟 p50 95.0ms / p95 108.0ms（n=40，同参数三次复跑 p50 94.8 / 95.4 / 95.0），打断 `barge_in → playback_stopped` p50 0.66ms（30/30 命中，旧轮违规 0 条），记忆检索热路径 50 条 1.8ms / 300 条 6.9ms。**这是 Mock 链路开销，不是真实用户延迟**：首字里约六成是 Mock LLM 桩自带的节奏，真实模型的首 token 时间会替换掉它。复跑：`.\.venv\Scripts\python.exe scripts\bench_voice_loop.py`。
要压进 500ms 需要原生全双工语音模型或 ASR Partial 抢跑——本项目**不追求**在级联管线下做到这一点。

**5. 检索开销在「行 marshal」，不在向量计算**
10k 条记忆实测（2026-10-25，记录在 `server/memory/store.py` 的 `retrieval_ranking_rows` 文档串）：读库 51ms + 建 `Memory` 对象 88ms = 166ms 的暖轮次里有 139ms 花在把整表行装成对象上，而被以为是大头的 `matrix @ qv` 只要 1.2ms。于是检索改成两段式——先用只含打分所需列的 `RankingRow` 排序，再按 id 取回存活的 top_k。要优化检索，先看 marshal 而不是向量库。

## 🛠️ 扩展与已知限制

**两步接入一个 Worker**

1. 在 `config/agents_roster.yaml` 加一行：`id` / `adapter` / 能力标签。
2. 内置适配器不够用时，继承适配器基类实现 `prepare_context / execute / cancel` 并注册即可——调度核心零改动，这条有测试背书（Mock 适配器跑通全程）。

**已知限制**

- 级联管线的首字延迟地板以 `docs/LATENCY_BASELINE.md` 的口径为准；Mock 数字不能当真实用户延迟用。
- 花名册里没写 `adapter`（或写成 `manual`）的 Worker 只组密件、不执行：督办会转成 BLOCKED 并明说「这个 Agent 还没接执行体」，宁可卡住也不伪造一次执行。要真干活，换成 `cli_subproc` / `http_api` / `mcp` / `durable`，不改 Python。
- 混合检索是字符 n-gram TF-IDF + 重要性 / 时近性重排，在百至千条个人记忆规模下零外部向量库依赖；上万条长文档建议在检索层加稠密向量。索引重建目前同步发生在快环内，随库规模线性增长（跟踪中，见仓库 issue #31）。
- 语音打断依赖浏览器 WebRTC AEC，扬声器大音量外放可能回声自激，建议戴耳机。

**像素世界目前不做的**（本仓冻结态，2026-10-25 分家前口径；世界新动向以新仓 roadmap 为准）

- **世界内验收 / 回写**：世界是纯投影，验收、打回、换将这些动作仍回主界面点。批复动作客户端已在 `pixel-world/src/world/commands.ts` 落地，但尚无生产调用方，只被单测引用；缺的是产品决策与 UI 接线。
- **真实的工具失败**：工坊有 `overheating` 态，但没有真实工具时伪造失败就是假动作，所以恒不触发。
- **造物主的 LLM 生成**：扩建走的是固定样板 + 槽位选择，生成层留着没做。
- 世界端 e2e 的 `toAltar` 采集窗口证据缺口仍在（`cs1lion/xiaoxi#9`）；同组的台账证据缺口（#10）已关闭。

## 📚 文档

- 协议与契约：[docs/PROTOCOL.md](docs/PROTOCOL.md)（不得自造 WS 事件 / API 字段）
- 测试与门禁：[docs/TESTING.md](docs/TESTING.md) · 延迟基线：[docs/LATENCY_BASELINE.md](docs/LATENCY_BASELINE.md)
- 慢环与 Worker：[docs/EXECUTIVE_SECRETARY_V3.md](docs/EXECUTIVE_SECRETARY_V3.md) · [docs/PI_DURABLE_MIGRATION.md](docs/PI_DURABLE_MIGRATION.md) · [agent-runtime/README.md](agent-runtime/README.md)
- 施工细节（环境变量、目录地图、接线点）：[docs/README_EXTENDED.md](docs/README_EXTENDED.md)
- 协作规矩（多 Agent）：[docs/AGENT_PLAYBOOK.md](docs/AGENT_PLAYBOOK.md) · [AGENTS.md](AGENTS.md)
- 像素世界历史存档（不再演进）：[pixel-world/README.md](pixel-world/README.md)
