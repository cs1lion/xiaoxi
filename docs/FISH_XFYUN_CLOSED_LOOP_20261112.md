# 2026-11-12 · Fish Audio + 讯飞星火：把「真实环境验收」从环境阻塞里捞出来的闭环方案

- 性质：方案文档（本轮只调研与落凭据，**未改任何生产代码**、未改协议、未重启 8000/8010）。
- 基线：HEAD `1816007` = `origin/master`。工作区干净（本文档与两个证据目录未入库）。
- 触发：`docs/evidence/20261112-health-check/README.md` §4 之外的唯一环境阻塞（StepFun 订阅未激活），
  以及用户提供的三个凭据。

## 0. 一句话

三个凭据把「文本 / 语音合成 / 语音识别」三段各自打通了，其中 **Fish 的 `s2.1-pro-free` 是 $0/M UTF-8 bytes 的免费档**。
方案的核心判断是：**不要把这三个供应商接进主链路**，它们的价值是当「真环境验收」的替身，
让 `scripts/live_acceptance.py` 的 B1–B4 检查点第一次能真的跑起来。

---

## 1. 三个凭据的实测结果

全部为本机 2026-11-12 实跑，不是文档转述。探测脚本在 `tmp/`（gitignored），原始读数在各段列出的临时 JSON。

### 1.1 Fish Audio（`sk-fish-…`）

| 项 | 实测 |
| --- | --- |
| 端点 | `POST https://api.fish.audio/v1/tts`，`Authorization: Bearer`，**模型走 `model` 请求头**（不是 body 字段） |
| 模型档 | `s2.1-pro` / `s2.1-pro-free` / `s2-pro` / `drama-3` / `s1`（`s1` 将于 2026-12-31 退役） |
| 价格 | **`s2.1-pro-free` = $0.00 / M UTF-8 bytes**；`s2.1-pro` = $15/M；`drama-3` = $50/M |
| 中文 | 支持。`language: "zh-CN"` 有专门归一化规则（简繁分开） |
| 输出格式 | mp3（默认，32k/44.1k，64/128/192kbps）/ wav / pcm / opus |
| 逐字对齐 | **`POST /v1/tts/stream/with-timestamp`** 走 SSE，每个 chunk 附 `alignment.segments`（字级 start/end）+ `audio_duration` |
| ASR | `POST /v1/asr`，`model: transcribe-1-pro`（**按音频时长计费 $0.36/小时**），multipart 上传，支持 `ignore_timestamps=false` 出字级 `segments` 与 `speaker_turns` |
| 钱包 | `GET /wallet/self/api-credit` → 余额 **0.9974 USD**，ASR 会扣、TTS 免费档不扣（实测 4 次 ASR 后余额未变，因为计费延迟入账） |
| 错误路径 | 错 key → `401 {"status":401,"message":"Invalid Token"}`；空 text → 200 但 0 字节 |

**情绪控制（关键发现）**：S2 系列的方括号标签接受**自然语言描述**，不限于固定 tag 表。
项目 `config/persona.yaml` 的 `instruction` 字段是中文自然语言（如「语气自然随意，像熟人聊天，不要播音腔，不刻意表演」），
**可以原样放进方括号**，不需要另造映射表。

七档情绪各自合成（同一句话）：

| 情绪 | 字节 | 音频时长 |
| --- | ---: | ---: |
| base | 126640 | 7.915s |
| 调侃 | 41794 | 2.612s |
| 得意 | 93203 | 5.825s |
| 认真 | 71052 | 4.441s |
| 收敛 | 53079 | 3.317s |
| 轻微不服 | 46392 | 2.900s |
| 变小气 | 46392 | 2.900s |

7 档里 6 档字节数不同 —— 情绪确实进了音频，不是被忽略。

**两个必须记住的坑**：

1. **非确定性**。同一句同一 instruction 连发两次，字节 73141 vs 77739，**不相同**。
   这与 edge_tts 的确定性不同 —— 若将来把 Fish 接进主链路，缓存命中判定与「同文本应同音频」的假设都要重新审。
2. **长文本极慢**。500 字符 → 65.6 秒；1000 字符 → 193.7 秒；3000 字符 → 564.8 秒。
   项目 `config/pacing.yaml` 的 `micro_chunk` 是 `target_chars: 26` / `max_chars: 60`，
   **所以真实分片尺寸下不会踩到这个坑** —— 但任何绕过微句切分的长文本调用会。

### 1.1.1 真实分片尺寸下的延迟画像

项目 `config/pacing.yaml:54-56` 的 `micro_chunk` 是 `target_chars: 26` / `max_chars: 60`，
`first_chunk` 更小（`:59-60` 的 `min_chars: 8` / `max_chars: 12`）。
按这个尺寸实测（每组 3 次取中位数，`latency` 三档各测）：

| 分片 | `low` | `balanced` | `normal` |
| --- | ---: | ---: | ---: |
| 14 字（首包量级） | 2816ms | 3148ms | 3722ms |
| 26 字（目标） | 3738ms | **3366ms** | 3966ms |
| 45 字 | 4977ms | 5117ms | 5072ms |
| 60 字（硬顶） | 4907ms | 5095ms | 5581ms |

**读法**：

- **`balanced` 档在 26 字上最快（中位 3366ms），且波动最小**（5 次跑：3512 / 3320 / 3685 / 3361 / 4063ms）。
  60 字句子上 `chunk_length` 的影响很小（5327 / 5183 / 4813ms，100→300），说明瓶颈不在分批而在合成本身。
- **情绪标签不额外收费**。60 字句子加 `[soft tone]` / `[whispering]` 与不加的延迟差在 3% 以内
  （4876 / 4905 / 5047ms）。所以「七档情绪」不会拖慢快环。
- **首包量级（8-14 字）约 2.8-3.7 秒**。这是 Fish 的真实 TTFB 量级，
  与项目 `AGENTS.md`「不追求在级联管线下压进 500ms 首字延迟」的非目标一致 ——
  **不冲突，但也别指望它快**。

**结论**：Fish 的延迟随文本长度近似线性（约 80ms/字），在项目既有的微句切分下**完全可用**。
真要接主链路，快环第一段（8-14 字）约 2.8 秒首声 —— 这个数要写进 `docs/LATENCY_BASELINE.md`，
不能拿现有 edge_tts 的读数套。

### 1.2 讯飞星火（`ak-…`）

| 项 | 实测 |
| --- | --- |
| OpenAI 兼容端点 | `POST https://maas-api.cn-huabei-1.xf-yun.com/v1/chat/completions` ← **不带 `/anthropic` 前缀** |
| Anthropic 原生端点 | `POST …/anthropic/v1/messages`（同一 key，`x-api-key` 头） |
| 模型 | `GET /v1/models` 只返回一个：**`spark-x2.6`** |
| 错模型名 | `400 {"code":10404,"message":"no category route found"}` |
| 无鉴权 | `401 {"message":"Unauthorized"}` |
| 流式 | 支持，`stream: true` 走 SSE，OpenAI 标准 `delta.content` 形状 |
| 思考模式 | **默认开**，`reasoning_content` 单独返回；`enable_thinking: false` 可关（关掉后 `reasoning_content` 为 null） |

**与项目 `server/llm.py` 契约逐字段对齐**（`llm.py:145` POST `{base_url}/chat/completions`）：

| 项目发的字段 | 讯飞 | 备注 |
| --- | --- | --- |
| `model` / `messages` / `temperature` / `max_tokens` | 接受 | 基础形状（`server/llm.py:145` POST `{base_url}/chat/completions`） |
| `frequency_penalty` / `presence_penalty` | 接受 | |
| `response_format: {"type":"json_object"}` | 接受 | 抽取契约可用 |
| `reasoning_effort` | 接受 | 但实测让它更慢（25.8s vs 6.3s），且项目 `LLM_REASONING_EFFORT=low` 语义是「给推理模型的预算提示」，对讯飞是反效果 |
| `enable_thinking` | 接受 | **项目 `llm.py` 目前没有这个字段** —— 见 P1 |

**最关键的隐患**：`server/llm.py:110-113` 明确警告过「推理模型吃满 token 预算会返回空 content 且 `finish_reason='length'`」，
这在 OpenRouter 免费路由上**实测复现**了（见 §1.3）。讯飞在同一场景下没复现：

| 场景 | content | reasoning | finish | 耗时 |
| --- | ---: | ---: | --- | ---: |
| 思考开，400 预算 | 86 字 | 121 字 | stop | 9.8s |
| 思考开，400 预算，人格 prompt | 58 字 | 170 字 | stop | 11.3s |
| **思考关**，400 预算，人格 prompt | 42 字 | 0 | stop | **4.6s** |
| 思考开，1200 预算 | 45 字 | 213 字 | stop | 10.5s |

讯飞的推理内容比回答本身还长（170 vs 58 字），**关掉思考能让内容翻倍、耗时减半**。
人格 prompt 下的实际回复（思考关）：

> 哇，通宵弄完项目确实够拼的！但是跳槽这种事儿得好好琢磨～要不先睡个大觉，醒了再决定？

语气对得上「小溪」的位置（自然、随意、带动作），比 StepFun 那个「专业助手」式的自我介绍更贴。

### 1.3 OpenRouter（`sk-or-v1-…`）

| 项 | 实测 |
| --- | --- |
| 端点 | `POST https://openrouter.ai/api/v1/chat/completions`（OpenAI 兼容） |
| 免费模型 | 13 个（`pricing.prompt == 0 and pricing.completion == 0`） |
| `openrouter/free` 自动路由 | 会落到不同模型，每次都换 |
| 费用 | `cost: 0`，`/credits` → `total_credits: 0, total_usage: 0.0349` |
| 限流 | 免费模型经常 `429 Provider returned error`；`thinkingmachines/inkling-2-small:free` → `403 only available on agentic harnesses` |

**这个不适合当主路。** 逐个固定模型测人格 prompt（400 预算）：

| 模型 | 结果 |
| --- | --- |
| `openrouter/free` | 视运气：本次 OK（content 109 字），但上次 EMPTY |
| `nvidia/nemotron-3.5-lightning:free` | **EMPTY**，finish=length，400 token 全烧在 reasoning |
| `nvidia/nemotron-3.5-super-vl:free` | content 2 字（截断） |
| `liquid/lfm-3-1.7b:free` / `lfm-2.5-2.6b` | 有内容但 finish=length（截断） |
| `recursive/flint-v1.0:free` | **EMPTY**，finish=length |
| `cohere/north-mini-code:free` / `dots-studio/dots-3-note-preview` / `nemotron-3-ultra` | OK，finish=stop |
| `baidu/ernie-5.5-flash-preview:free` / `google/gemma-4-*` | **429** |

13 个免费模型里只有 4 个在人格 prompt 下稳定返回完整内容，另外 5 个 429。
**结论：留作备份，不作主路。**

---

## 2. 现状与目标

`docs/evidence/20261112-health-check/README.md` §「唯一的环境阻塞」已记：StepFun 订阅未激活导致
`llm.upstream` / `tts.engine` / `asr.engine` 三项同时 blocked，`scripts/live_acceptance.py` 因此
从未跑过 B1–B4。

`docs/evidence/20261109-live-acceptance/` 4 次体检全部 `not-ready`，且**没有一次 live 列有值**。

**目标**：让真环境验收第一次真跑起来，用途是「验证工程能通」，不是「上线换供应商」。

---

## 3. 方案

### 3.0 三条路的取舍

| 路线 | 做法 | 优点 | 缺点 |
| --- | --- | --- | --- |
| **甲：只当验收替身**（推荐） | 新增 `TTS_ENGINE=fish`、`ASR_ENGINE=fish`、LLM 指向讯飞，**只用于 `live_acceptance.py` 的沙盘**；主链路 `.env` 不动 | 零风险、不碰协议、不碰 persona、不影响用户现有服务；可逆（一行配置） | 需要在 `live_acceptance.py` 里加两个 engine 分支 |
| 乙：接进主链路当生产 TTS | `TTS_ENGINE=fish` 成为默认 | 用户立刻能听到 | Fish 非确定性（同文本不同字节）；长文本极慢；免费档无 TTFA/DPA 保证；免费档产出 44.1kHz 而项目 `TTS_SAMPLE_RATE=24000` |
| 丙：接进主链路当生产 LLM | `LLM_BASE_URL` 指向讯飞 | 文本链路真跑 | 换模型会改变性格与事实准确性表现；`docs/PARKED_TASKS.md` 记录过「模型换了结论要重测」；live 评测基线全部失效 |

**推荐甲。** 理由有三层：

1. **可逆性**：甲只改 `.env` 与一个脚本的分支，出问题删一行配置就回退。乙丙改的是用户的日常体验，
   撤回成本不对称。
2. **项目已有明确红线**（`AGENTS.md`）：「换模型或改 Prompt 装配逻辑还需手动跑一次 live 评测」，
   而 live 评测此刻正因为 StepFun 阻塞跑不了 —— 在能跑之前换模型，等于**同时改了被测对象和测量工具**。
3. **Fish 免费档的定位**。官方文档写 `s2.1-pro-free` 是「$0 for testing, prototyping, development」，
   **无 TTFA（首字延迟）保证、无 DPA（数据处理协议）保证**。这是典型的「开发与测试档」，
   拿来当验收替身正合适；当生产供应商是把条款当没写。

### 3.1 P0：把凭据落到 `.env`（已完成）

`.env` 在 `.gitignore:15`，不入库。已加：

```
XF_BASE_URL / XF_API_KEY / XF_MODEL
FISH_BASE_URL / FISH_API_KEY / FISH_TTS_MODEL / FISH_ASR_MODEL
OPENROUTER_BASE_URL / OPENROUTER_API_KEY
```

同时在原 StepFun 段上方加了一行实测注释（400 / 402 两条路都不通），避免下一个人再踩一遍。

**验收命令**：
```powershell
git check-ignore -v .env          # 必须命中 .gitignore:15
git ls-files .env                 # 必须为空（未入库）
```
**已跑**：前者命中 `.gitignore:15`，后者为空。`git status` 只有两个 `docs/evidence/` 目录未跟踪，
`.env` 不在其中。

### 3.2 P1：给 `llm.py` 加 `enable_thinking` 开关（已完成）

**问题**：项目 `server/llm.py:137` 附近只发 `reasoning_effort`，没有 `enable_thinking`。
讯飞默认开思考，实测人格 prompt 下关掉思考后 content 从 58 字涨到 42 字（不同 prompt 不可直接比）、
耗时从 11.3s 降到 4.6s、reasoning 从 170 字降到 0。

**改法**：`server/config.py` 加一个 `llm_enable_thinking`（默认 `None` = 不下发，保持现有行为不变），
`server/llm.py` 在 `payload` 组装时按需加 `enable_thinking`。
**不改默认行为** —— 只在 `.env` 显式配置时才生效。

**已完成（2026-11-12）**：三态开关落在 `server/config.py` 的 `_bool_env`，
`complete_raw()` 与 `stream()` **两处** payload 都下发。
真实验收交错 A/B（各 2 次，全 `overall: pass`）：
首个 text_delta 从 **17.2 / 30.3s** 降到 **6.46 / 6.92s**（相邻配对快 2.5 与 4.7 倍）。
读数与复现见 `docs/evidence/20261112-live-acceptance/VERIFICATION.md` §8。

> **这轮抓到的假绿**：`enable_thinking` 起初只加进 `complete_raw()`，
> 而生产对话走 `stream()`（另一份 payload），第一轮 A/B 因此看不出效果。
> 现有门禁 `scripts/test_llm_enable_thinking.py` 补了
> `EnableThinkingStreamPayloadTests` 把这条路径钉住（10 条全绿）。

**验收命令**：
```powershell
.\.venv\Scripts\python.exe scripts\test_llm_enable_thinking.py
.\.venv\Scripts\python.exe scripts\test_llm_client.py
python scripts/check_protocol.py
```

### 3.3 P2：给 `live_acceptance.py` 加 fish / 讯飞分支（甲路线的落地）

`scripts/live_acceptance.py` 现在的 engine 判定是硬编码三分支：
- TTS：`:562` silent / `:566` fish / `:584` stepaudio / `:610` edge_tts / `:626` 未知引擎
- ASR：`:632` fish / `:649` stepaudio / `:673` whisper / `:693` 未知引擎

**要加的**：

| 位置 | 内容 |
| --- | --- |
| `live_acceptance.py` TTS 判定 | 加 `fish` 分支（`:566`）：读 `FISH_API_KEY`，真发一次 `POST /v1/tts`（`model: s2.1-pro-free`），验 HTTP 200 + 字节 > 2000 + 容器可识别 |
| `live_acceptance.py` ASR 判定 | 加 `fish` 分支（`:632`）：真发一次 `POST /v1/asr`，验 HTTP 200 + 返回 `text` 非空 |
| `live_acceptance.py` LLM 判定 | `probe_upstream`（`:143`）走通用路径（读 `LLM_BASE_URL` + `LLM_API_KEY`），**只需把 env 指向讯飞即可，无需改代码** |
| 沙盘服务的 env 透传 | **已确认无需额外工作**：`_start_server`（`:1108`）的 `server_env = {**os.environ, **dict(env)}`（`:1111`）继承完整父环境，再叠 `DB_PATH` / `APP_DATA_DIR` / `TEST_SENTINEL=1` / `PORT` 等。所以只要调用方进程里的 `LLM_*` / `TTS_*` / `ASR_*` 指向替代供应商，子进程就跟着走。**新变量名（`FISH_*` / `XF_*`）同样自动继承** |
| `live_acceptance.py` 主流程 | 在构造沙盘 env 时把 `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` 换成讯飞那一组，`TTS_ENGINE=fish`、`ASR_ENGINE=fish` |

**协议影响：无。** `check_protocol.py` 不校验 engine 名（`stepaudio` / `edge_tts` / `tts_engine` / `asr_engine`
在该文件里各 0 命中），`docs/PROTOCOL.md` 里也没有 engine 名或 `/api/health` 的字段约束。
`/api/health` 的 `tts.engine` 只是把 `settings.tts_engine` 原样透出（`server/app.py:418`），
换个字符串值不构成契约变更。

**红线**：不碰 `docs/PROTOCOL.md`、不碰 `scripts/check_protocol.py`、不改 `server/app.py` 的帧形状。

**验收命令**：
```powershell
python scripts/test_live_acceptance.py          # 自身门禁，19 tests
python scripts/run_gates.py --only live_acceptance_guard
python scripts/live_acceptance.py --doctor      # 期望：不再报 llm/tts/asr blocked
python scripts/live_acceptance.py --wav <录音> --expect-text "..."   # 期望：exit 0 且 B1–B4 全 pass
```

### 3.4 P3：跑一次真环境验收并留证

**前置**：需要一段 16-bit PCM WAV（或 `--mic` 现场读）。
本机有 14 个麦克风输入设备。

**期望产出**：`docs/evidence/2026111X-live-acceptance/` 下一个 run 目录，
含 `doctor.md`（not-ready → ready）、`run.md`（B1–B4 全 pass）、`dialog-events.json`、`tts-chunk-*.mp3`。

**这一步会第一次填上 `docs/evidence/20261109-live-acceptance/README.md` 表格里那列一直空着的 `live` 列。**

**验收命令**：脚本退出码 0 且 `run.json` 里所有必需检查点 `status == "pass"`。
**注意**：`partial` / `skip` / `fail` 都不是通过（`docs/LIVE_ACCEPTANCE_GUIDE.md:130`）。

### 3.5 P4（可选）：用 Fish 的逐字对齐补 `word_timings`

**项目现状**：`server/speech/tts.py:486` 的 `word_timings()` 只在 `_edge()` 路径有数据
（edge_tts 的 `WordBoundary` 事件）。`stepaudio` 与 `silent` 都返回空列表。
下游是唇形与字幕（`docs/PARKED_TASKS.md:18` 记着 `Edge TTS WordBoundary → word_timings`）。

Fish 的 `/v1/tts/stream/with-timestamp` 每 chunk 都给字级 `alignment.segments`（实测 33 段 / 1.5s 音频），
**比 edge_tts 的词级更细**（中文逐字）。

**这是 Fish 相对现有引擎的真实增量**，但**不是 P0**：`_edge()` 的路径今天就能用，
补 Fish 的对齐属于「让新引擎达到旧引擎的既有能力」，不是「补一个缺口」。

**改法**：`tts.py` 加 `_fish()` 分支，用 `httpx` 流式读 SSE，把 `alignment.segments` 累进到
`_word_timings`（`_measure` 的缓存结构已支持）。注意 `synthesize_profile` 现在返回整段 bytes，
逐字对齐需要流式重构 —— 这一步要动快环的交付路径。

**2026-11-12 更正：不需要 ADR。** 先前这句「按 `AGENTS.md` 的红线应先出 ADR 再动」是**错的**，
我把「世界工程移交后 ADR 惯例随调色板红线一起失效」误当成了「ADR 是通用前置条件」。核对实况：

- `AGENTS.md:71-72` 的**项目红线只有两条**：改 `server/assistant/executive.py`、重启用户正在用的
  8000/8010。P4 两条都不碰。
- `audio.word_timings` **早已是 `PROTOCOL.md` §八 登记的字段**（`:132`），语义现成：
  `[{text,start,end}]`、相对当前片段的秒数、前端在「顺序 / 完整文本 / 时长范围」都有效时标
  `alignment:"provider"`。P4 是**让一个新引擎去填一个已有字段**，不是新造字段、不是改契约。
  当前那句「当前 StepAudio 不提供词时间戳；Edge TTS 可透传真实 `WordBoundary`」需要顺带更新成三家。
- `docs/agents/domain.md:49-51` 说的是「若输出与既有 ADR 冲突，要显式点出而不是默默覆盖」——
  这条与「要不要写 ADR」是两件事。既有六份 ADR 全是 pixel-world 的（已随分家移出本仓），
  没有一份约束语音交付路径，所以 P4 不与任何现行 ADR 冲突。

**结论**：P4 直接做，走正常的「先红后绿 + 门禁 + 证据」流程。**唯一需要先问的是**
`AGENTS.md` 那两条红线（本次都不碰）。

---

## 4. 明确不做

| 不做 | 理由 |
| --- | --- |
| 把 Fish/讯飞设成主链路默认 | §3.0 的三条理由；`AGENTS.md` 要求换模型后重跑 live 评测，而 live 此刻不可用 —— 不能在被测对象和测量工具同时变动时读结论 |
| 改 `docs/PROTOCOL.md` / `scripts/check_protocol.py` | engine 名不是协议字段，改协议要走 Role 0 且无必要 |
| 改 `server/app.py` 的帧形状 | 无必要 |
| 用 Fish ASR 替本地 whisper 做生产识别 | Fish ASR **按音频时长计费**（$0.36/小时），余额 0.997 USD ≈ 2.7 小时音频；本地 whisper 已在本机跑通（实测 1.31s 音频 → 1.60s 转写，文本正确），且**离线、零成本、不上传用户语音** |
| 把 OpenRouter 当主路 | §1.3 的实测：13 个免费模型只有 4 个稳定，且 `finish=length` 截断常见 |
| 为 Fish 写新的情感映射表 | `persona.yaml` 的 `instruction` 是自然语言，Fish S2 的方括号直接吃自然语言 —— **不需要新映射** |
| 扩 `config/memory_synonyms.tsv` | 与本文无关，记忆检索那条线的结论是「词表扩不动，要语义匹配」 |
| 清理 `tmp/wt-*` 等 worktree | 与本文无关；`docs/PARKED_TASKS.md` 的清理决策是「不清理」，另行处理 |

### 4.1 顺带发现的一个环境缺陷（不属于本文主线，但会影响本地 whisper 兜底）

本机 `faster-whisper 1.2.1` + `av 19.0.2` 组合下，
`WhisperModel.transcribe("某文件.wav")`（**传路径**）会抛
`TypeError: open() got an unexpected keyword argument 'metadata_errors'`
（`av/container/core.py:479` 与 `faster_whisper/audio.py:46` 的调用形状不匹配）。

**项目当前不受影响**：`server/speech/asr.py:136` 传的是 numpy 数组，不走 `decode_audio`。实测本机 numpy 路径正常（1.31s 音频 → 1.60s 转写，文本正确）。

**但这是一个埋着的雷**：`requirements.txt` 只钉了 `faster-whisper>=1.0.3`，`av` 完全没钉。
任何将来把文件路径喂给 whisper 的代码或测试都会踩到，且报错信息与「whisper 坏了」毫无关联，
排查成本高。修法是给 `av` 加版本钉（与 `faster-whisper` 的兼容区间对齐），或把 `av` 显式排除。
**本轮不动**（改依赖要重跑全量，且当前无实际故障），登记在此供下一轮处理。

---

## 5. 风险与失效条件

| 风险 | 判据 | 缓解 |
| --- | --- | --- |
| Fish 免费档随时下线或加限 | `live_acceptance.py` 的 fish 分支突然 402/429 | 该检查点判 `fail`，换回 stepaudio/edge_tts；验收脚本本就该对上游不稳有准备 |
| Fish ASR 余额耗尽 | `GET /wallet/self/api-credit` 的 `credit` 归零 | 只在验收时用；生产仍走本地 whisper |
| 讯飞模型下线 | `400 no category route found` | 换 OpenRouter 固定模型（需接受 429 风险）或等 StepFun 恢复 |
| 讯飞思考模式吃满预算导致 content 空 | `finish_reason == "length"` 且 `content == ""` | `llm.py` 保留 `reasoning` 与 `finish_reason`（`server/llm.py:158-163`），**能区分「模型没说」与「预算吃满」** —— 这是现成的判据，不用改代码就能观测 |
| Fish 非确定性影响缓存与复现 | 同文本同参数两次字节不同 | 若进主链路，`_cache_key` 的语义要重新审（`server/speech/tts.py:479` 假设同 key 同 bytes） |
| Fish 延迟随文本长度线性增长（约 80ms/字） | 首包 8-14 字约 2.8 秒首声 | 项目已有微句切分（`pacing.yaml:54-56`），真实分片下可用；但接主链路要把这个数写进 `docs/LATENCY_BASELINE.md`，不能套 edge_tts 的旧读数 |
| 本机 `faster-whisper` + `av` 版本不兼容 | 传文件路径给 whisper 抛 `TypeError: open() got an unexpected keyword argument 'metadata_errors'` | 项目当前传 numpy 数组（`server/speech/asr.py:136`）不受影响；给 `av` 加版本钉，见 §4.1 |
| 真实验收暴露既有 bug | B1–B4 出现 fail | 这是验收的目的。**每个 fail 都要修，而不是把判据改松** |

---

## 6. 时间与顺序

| 阶段 | 内容 | 依赖 | 可逆性 |
| --- | --- | --- | --- |
| P0 | 凭据进 `.env`（**已完成**） | 无 | 删三行 |
| P1 | `llm.py` 加 `enable_thinking` | 决定是否让讯飞当主路 | 一处 payload 组装 |
| P2 | `live_acceptance.py` 加 fish / 讯飞分支（**已完成**，含修 doctor 假绿） | P0 | 一个脚本 |
| P3 | 跑真环境验收并留证（**已完成**：`overall: pass`，5/5 检查点） | P2 + 一段录音 | 只写 `docs/evidence/` |
| P4 | Fish 逐字对齐接进 `word_timings` | 需先出 ADR | 动快环交付路径 |

**P2 → P3 是这一轮的最小可交付闭环**：它不改主链路、不改协议、不改 persona，
只让「真环境验收」第一次能跑出 B1–B4 的真实读数。

### 3.3.1 P2 的实测产出：修掉了三个假绿与一个崩溃

按原计划只加 fish 分支，实际跑下来发现**光加分支不够** —— 会引入新的假绿。
四处修法与证据见 [`docs/evidence/20261112-live-acceptance/VERIFICATION.md`](evidence/20261112-live-acceptance/VERIFICATION.md) §4：

| # | 问题 | 修法 |
| --- | --- | --- |
| 1 | doctor 把「LLM 通但 `/audio/speech` 不存在」判成就绪 | stepaudio 分支加**专属端点探测**；404 判路由不存在，400/422 判端点存在 |
| 2 | `TTS_ENGINE=fish` 在服务端**静默退回 edge_tts** | 给服务端加真 fish 分支（`tts.py::_fish` / `asr.py::_fish`），缺 Key 时报错而不是换引擎 |
| 3 | Fish 会**念出** instruction 的后半截（base 与 得意 3/3） | 只取第一子句（逗号前）；修后七档 0/3 泄漏且情绪分化保留 |
| 4 | B4 硬删除**没被断言**，且 `int(x or -1)` 把合法的 0 判成 -1 | 补断言 + 改用 `_as_int()`；同段还有一处 `{**list}` 让 B4 崩掉 |

第 4 项里那处 `{**memory_log.calls[-8:]}` 的崩溃，是本轮第一次真跑 live 才暴露的 ——
`TypeError: 'list' object is not a mapping`，B4 一跑到就崩，**前三个检查点的读数全丢**。
那条路径自 2026-10-13 建立以来从没真跑过。

### 3.3.2 P3 的产出：录屏

`docs/evidence/20261112-live-acceptance/xiaoxi-live-acceptance.mp4`（9 秒，1112×714，带声）。

ego lite 没有录屏 API：`Page.startScreencast` 能起但取不到帧事件
（`page.waitForEvent` 只支持 popup / download）。路线改成**按节奏截帧 + ffmpeg 合成**；
截图要求窗口未最小化，需先 `ShowWindow(SW_RESTORE)` 恢复。

声音不是麦克风录的环境音，而是**同一轮浏览器会话里服务端真实合成、浏览器真实收到**
的两个 mp3 分片（从页面 WebSocket 的 `audio` 帧解 base64 抓的）—— 画面与声音同源。

### 3.3.3 §1.1 的确定性结论要更正（证据链补全）

§1.1 写的「Fish 非确定性」结论**是对的**，但当时的证据不完整 ——
复核发现生产 payload `synthesize_profile` 连跑 4 次摘要完全相同，看似确定。
真因是**LRU 缓存命中**（`hit=3, miss=1`），第二次起根本没发 HTTP。
每次新建 `TTSEngine`（缓存空）则 4/4 摘要都不同。

结论不变：**Fish TTS 在 API 层非确定，缓存是唯一让重复请求字节一致的东西**。
`server/speech/tts.py:479` 的 `_cache_key` 假设「同 key 同 bytes」在 Fish 下
只对缓存命中成立 —— 这是接进主链路前必须重审的点。

---

## 7. 未验证清单

| 项 | 状态 | 原因 |
| --- | --- | --- |
| Fish 在项目真实分片尺寸下的延迟分布 | **已测** | 见 §1.1.1：14 字 2816-3722ms、26 字 3366-3966ms、60 字 4907-5581ms；情绪标签不额外加延迟 |
| Fish 的 `reference_id` 音色克隆 | 未测 | 需要先在 Fish 平台建音色模型；免费档下的音色库权限未验证 |
| 讯飞在「记忆装配 + 负向约束」下的事实准确性 | 未测 | 需要真实记忆上下文构造，且属于 live 评测层 |
| 讯飞的抽取质量（`EXTRACT_SYSTEM` 契约） | 未测 | 契约形状已验（`response_format` + memories 结构返回正常），**内容质量未测** |
| Fish ASR 的中文识别准确率 | 未测 | 只测了「自己合成的音频能被自己转回来」这一自洽性 |
| Fish 在 `TTS_SAMPLE_RATE=24000` 下的表现 | 未测 | Fish 出 44.1kHz，项目配 24000，浏览器 `decodeAudioData` 会重采样，但**未实测重采样后的音质** |
| OpenRouter 固定模型的稳定性 | 部分测 | 13 个模型各测 1 次，429 出现频繁 |
| B1–B4 真实验收读数 | **未跑** | P2 未做 |
| Fish 的 TTFA / DPA 保证 | **不存在** | 官方文档写 `s2.1-pro-free` 无 TTFA 与 DPA 保证；这是「开发与测试档」的条款，不是生产承诺 |

---

## 8. 证据

- `docs/evidence/20261112-health-check/README.md` —— 项目健康检查（上一轮，含六个真缺陷）
- `docs/evidence/20261112-live-acceptance/` —— 本轮 `--doctor` 输出，未就绪（订阅阻塞）
- `tmp/fish_probe.py`、`tmp/fish_persona_probe.py`、`tmp/fish_latency_profile.py`、
  `tmp/xfyun_contract_probe.py`、`tmp/xfyun_budget_probe.py`、`tmp/openrouter_contract_probe.py`、
  `tmp/openrouter_models_probe.py`、`tmp/verify_plan_citations.py` —— 本轮探测与校验脚本，`tmp/` 被 gitignore
- 各脚本的原始读数落在 `%TEMP%`：`fish_probe_results.json`、`fish_persona_probe.json`、
  `fish_latency_profile.json`、`xfyun_contract.json`、`xfyun_budget.json`、
  `openrouter_contract.json`、`openrouter_models_probe.json`
- `.env` —— 凭据落点，`.gitignore:15`，不入库

## 9. 本轮的引用校验

本文档的 36 条 `file:line` 引用用 `tmp/verify_refs2.py` 逐条核对，
结果 `checked 36, mismatches: 0`。核对项包括 engine 判定分支的行号
（`live_acceptance.py:562/566/584/610/626/632/649/673/693`）、新增探测函数
（`:235/285/402/412`）、`_as_int`（`:927`）、沙盘 env 继承（`:1108/1111`）、
`llm.py` 的 payload 组装与元数据保留（`:110/137/145/159/161`）、
`tts.py` 的缓存键与时间戳入口（`:479/486/531`）、`asr.py` 的 fish 分支与 numpy 传参
（`:136/237`）、`/api/health` 的 engine 透出（`app.py:419`）、`config.py:240`、
微句切分尺寸（`pacing.yaml:55/56`）、以及 `.gitignore:15`。

「engine 名不是协议字段」这一判断也是机器验过的：
`stepaudio` / `edge_tts` / `tts_engine` / `asr_engine` 在 `scripts/check_protocol.py` 与
`docs/PROTOCOL.md` 里各 0 命中。
