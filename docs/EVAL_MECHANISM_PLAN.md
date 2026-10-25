# 小溪评测体系构建方案

日期：2026-10-25
依据：`docs/EVAL_MECHANISM_RESEARCH.md`（外部一手调研，含逐条 URL）；本轮工作流对现状的三路对抗核实（外部参考 / 内部摸排 / 覆盖矩阵）与完整性质检。
状态：**T1–T6 全部落地，T5 已 live 验收通过；余两项既有检查缺口待用户拍板**。截至 2026-10-25 已落地：T1 报告层（`scripts/evals_report.py` + `run_gates` 的 `evals_report` 门禁）、T2 `origin` fail-fast、T3a 日志漏斗（`scripts/mine_eval_candidates.py` 及其测试）、T3b `test_memory_eval.py` 内联夹具化（19 条真跑）、T4 `barge-in-timeline-offline`（offline 层 6→7 条）、T5 judge 三态**已 live 验收通过**（`evals/checks.py` 的 `judge_hallucination`、`run_evals.py` 的三态聚合与 `classify_results` 退出码分桶、`llm_judge_*` 配置、`scripts/test_judge_three_state.py` 37 条其中 14 条走真实 `run_case` / HTTP / `main_async` 路径；`--live --case hallu-empty-retrieval-live` 实测 3/3 通过、0 review）、T6 其余 5 个程序性记忆测试由「整组 SkipTest」改为「逐条/逐类 skipUnless」（25 条转真跑，34 条按名跳过）。
**余两项待用户拍板**（2026-10-25 live 全层实跑发现，均非本轮改动引入，已用 git diff 证实两个检查函数体未被触碰）：① `accuracy-over-persona-live` 的 `fact_correction_earth` 正则漏判多种自然纠正措辞；② `emotion-tag-retention-live` 偶发缺 `[情绪]` 标签或空回复。实锤样本见 `evals/results/20261025T064545Z.json` 与 `20261025T065401Z.json`。

**离线阶段抓到并修掉的一个真 bug**（2026-10-25）：`_judge_client()` 最初只给 `SimpleNamespace` 传了四个 `llm_*` 字段，而 `LLM.complete_raw` 还会读 `llm_temperature` / `llm_max_tokens` / `llm_frequency_penalty` / `llm_presence_penalty` / `llm_reasoning_effort`（`server/llm.py:109-121`）——真实调用会 `AttributeError`，又被 `judge_hallucination` 的 `except Exception` 吞成「review」，看起来像"裁判没配"实为配置残缺。原有的 12 条单测把 `_judge_client` 整个 patch 掉，从未走到这条真实路径。已改为从 `settings.as_dict()` 全量派生再覆盖四个 judge 字段，并补回归测试 `test_judge_client_construction_survives_real_complete_raw_fields`（用不可达地址把请求打到建 payload 之后，证明失败原因是连网而非缺字段）。这类"静默降级"正是 paid live run 里最难发现的一类，值得在接任何外部裁判前先补。

**离线阶段抓到并修掉的第二个真 bug**（2026-10-25）：三态的退出码语义曾与三方文档相反——`main_async` 按 `passed=False` 把 review 轮次扫进 `failures`，于是 judge 一没配或一调用失败，整条 live 门禁就退出码 1：**红的不是被测系统，是评测配置**。三方都写着"只有 fail 计入退出码"（方案 §四、`docs/TESTING.md`、以及代码自己打印的那行"未计入通过也未计入失败"），实现却恰恰相反。已抽出纯函数 `classify_results()`（优先级 xfail → fail → review → pass；缺 `verdict` 字段的旧形状 fail-closed，不让漏判静默变成放行），`main_async` 改为只由 `failed_runs` 决定退出码，汇总行加 review 计数；并补 8 条分桶回归 + 4 条 `main_async --live` 端到端（`RESULTS_DIR` 指向临时目录，不写 `evals/results/` 污染真实证据）。

补那 4 条端到端时又抓到两个**我自己写错的测试** bug，值得记下来：① 默认用含正则命中词的回复当被测输出，导致测试里混进真正的 fail，不是想要的 review 场景；② `patch.object(server.llm, "LLM", FakeLLM)` 把类整个换掉，而 `_judge_client()` 也要 `from server.llm import LLM`——裁判拿到假客户端、构造即 `TypeError` 被吞成 review，于是"裁判说 fail"那条测试**因为错误的原因通过**（正则 fail → exit 1），裁判压根没被调用。已改为 patch `LLM.complete_raw` 并按 `model` 分发台词。第 ② 条是"绿得不对"的典型：断言全过但被测路径从未执行，比红灯更难发现。

---

## 一、一句话结论

不推倒重来，**在现有 runner 上加一层「报告」，把散落的评测收进三个分区，用一条 origin fail-fast 让「强制溯源」名实相符**。总计约 600–900 行新增、集中在 `scripts/` 与 `evals/`，分 7 张票落地，每张都可独立离线验收与回滚。

---

## 二、现状判定（2026-10-24 实况，数字带当日锚）

| 事实 | 证据 |
|---|---|
| 结构是「一门禁 + 一 runner + 一堆不进门的评测」 | `run_gates.py` 的 `GATES` 实有 71 条（当日逐条枚举）；`run_evals.py` 的 offline 层是唯一进 CI 的评测 runner（`ci.yaml:49`）；11 个 `eval_*.py` 与 9 个 `test_*eval*.py` 全是按需手动 |
| 设计约束覆盖扎实：AGENTS.md 六/七条约束中 6 条完整覆盖，Worker 可插拔那条在 `run_gates` 的 `adapter_ext` 而不在 `evals/` 体系 | `evals/cases/` 6 条 offline 中 5 条对应约束（`lesson-injection-offline` 属程序性记忆检索，在约束矩阵外）；准确性优先与情绪标签的线上保持是 live-only；`run_gates.py:107-108,387` |
| 但「逐条有 test_core 断言」言过其实：仅三档 / 空检索 / 硬删除三条有直接断言；准确性与快环两条在 `test_core` 全无对应；情绪标签只有 `demo_turns()` 计数的弱断言 | `test_core.py:100,124,94`；登记表 `test_core.py:510-526` |
| 产出无人消费：`evals/results/` 已积 152 个运行快照（当日实枚举，数量在漂），全仓无任何读取方 | 除 `.gitignore:61` 与 `run_evals.py:257-261` 的写入方外零读取 |
| 通过率只有单一数字：`passed_runs/total_runs`，不区分「最低单轮通过率」与「平均通过率」 | `run_evals.py:268,280-281` |
| 全部 9 条用例都是 `pass_rule: all + runs: 3`，单轮抖动即翻转整条；`min_rate` 分支是无用例覆盖的死代码 | `run_evals.py:207-218`；`evals/cases/` 全量检索 |
| CI 假绿：6 个 `scripts/test_*eval*.py` 因夹具随 `/eval` 清理丢失而 `SkipTest`，`ci.yaml:71-78` 的循环把跳过算通过 | `docs/TESTING.md:51-58`；`ci.yaml:5` 注释「跳过计通过」 |
| offline 零网络零 Key 是**主动设防**而非天然无害 | `evals/checks.py:140` patch 掉 `complete_raw` + `executive.py:28,35` 的 `self.llm=None`；`:130-133` 记录去掉 patch 会退化成真实调用 |
| live 是「装配级」不是生产全链路 | `run_evals.py:116-117`；`docs/TESTING.md:43` 明确划界 |
| `server/assistant/evaluator.py` 不是对话质量评测器 | 两层全确定性，评慢环 Worker 交付物；`evaluator.py:1-11` 文件头自述 |

**判断**：门禁不严，缺的是**消费**。已跑过 152 次评测，没有任何东西读它。收益最大、风险最小的一侧是补报告层，而不是动门禁。

---

## 三、三层分区（词汇统一）

项目里 offline/live 已经叫「两层」，再引入「双轨」会出现第三套分层词汇。本方案只用三个词，**按行为分区，不按文件前缀分区**：

| 分区 | 进退出码 | 装什么 | 判定器 | 谁跑 |
|---|---|---|---|---|
| **门禁**（gate） | 是，失败即非零 | offline 6 条回归用例 + 新增的 barge-in 事件链用例 + 内联夹具化后的 `test_*eval*.py` | 纯代码（contains/regex/custom/事件序） | CI 与 `run_gates` 自动跑 |
| **报告**（report） | 否，只出报告 | `evals_report.py` 读 `evals/results/` 快照：趋势、配对差异、成本 | 纯代码（统计） | `run_gates` 里作为一条「报告跑得完」的断言；不进 `ci.yaml` |
| **探针**（probe） | 否 | live 三脚本、程序性记忆家族、judge 型用例 | 正则 / custom / judge / 人工 | 手动 `--live`，需 Key |

分区的判据写进文档：**是否调用真实模型、是否有固定预算**——而不是文件叫不叫 `eval_`。这直接消掉 `docs/TESTING.md:312` 按前缀划线造成的歧义。

「毕业」机制（Anthropic capability → regression）：探针区里同一条用例在**3 个不同日期**的实跑通过率都达到通过线，就提升为门禁回归用例，`origin` 记来源。回归集靠这个机制自然生长，不靠一次性设计完美。

---

## 四、判定器栈

一手排序是「代码 > LLM 裁判 > 人工」（[LangChain Agent Evals FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）：能用确定性检查就用（快、便宜、零歧义），LLM-as-judge 只处理代码判不了的主观维度，人工用于抽样校准前两者。

落到本项目：

1. **代码**（门禁 + 报告两层的主力）：现有 `contains / not_contains / regex_any / not_regex / custom` 五种 check 继续承担全部 offline 判定；新增的 barge-in 事件链也用事件序断言而非墙钟（`evals/checks.py:74` 已是这个模式）。
2. **LLM 裁判**（只加在 live 探针层）：新增 `check.type: judge`，复用 `run_evals.py:91-96` 已支持的 awaitable custom 通道，不需要新调度机制。三态输出 `pass / fail / review`：judge 缺 Key、调用异常、与正则不一致一律记 `review` 不记 `fail`。去偏手段只用论文本身支持的：交换顺序跑两遍、只在两次一致时采纳、否则记平局，并要求 judge 先给思维链（[arXiv:2306.05685](https://arxiv.org/abs/2306.05685)）；长度受控估计另引 [arXiv:2404.04475](https://arxiv.org/abs/2404.04475)。**judge 永不进 CI**（CI 无 Key，`ci.yaml:11` 明示）。
3. **人工**：live 落盘的原始回复已有（`run_evals.py:201-204`，只存回复与判定、不存 prompt 与凭据），补一份抽查清单即可，不建新机制。

---

## 五、评测集机制

### 5.1 origin fail-fast（T2，约 5 行）

`origin` 目前只是文档约定，`run_evals.py:44-52` 的 `load_cases` 不校验。加 fail-fast：缺失或空串则收集用例名，在 `main` 打印并 `return 1`。让「`origin` 必填」（`docs/TESTING.md:59`）从愿望变成机械保证。现有 9 条 origin 均非空，不会误伤。

### 5.2 日志漏斗（T3，约 150–200 行 + 80–120 行测试）

`scripts/mine_eval_candidates.py`：

- 纯 `sqlite3` 以 `mode=ro` 打开 `data/memory.db`（先例 `scripts/archive_durable.py:38`、`scripts/backup_local_databases.py:100`），**不得 import `server.memory.store`**（那会按 `server/config.py` 的 settings 打开活库、可能触发真实副作用）。
- 读 `messages` 表（schema 抄 `server/memory/store.py:80-88`，时间窗范式照 `store.py:1096-1100`），用确定性启发式提名四类候选：共指/省略追问（"那个/它/上次/我们之前/还记得吗"）、疑似幻觉面（空检索历史 + 共同经历断言）、事实挑战（"其实是…/不对吧"）、负面情绪边界。归一化去重与打标。
- 输出 JSON 到 `eval/research/case_candidates_<stamp>.json`（`.gitignore:54` 已覆盖 `/eval/`），字段 `heuristic / session_id / created_at / user_text / assistant_text / suggested_layer / suggested_origin`，内置凭据与个人信息正则脱敏（思路可复用 `scripts/eval_memory.py:32-35` 的 `_CREDENTIAL`）。
- **不写 `evals/cases/`**。候选经人工脱敏与改写后才手写进受版本控制的目录——这是隐私红线：`evals/cases/` 受版本控制，`.gitignore` 只覆盖 `/eval/`（:54）与 `evals/results/`（:61）。
- 不 import 任何 server 模块、无网络无 Key；库不存在时打印说明并 `exit 0`。

### 5.3 防过拟合

污染风险主要不是公开基准污染（本项目不测公开基准，`EVAL_MECHANISM_RESEARCH.md` §2.2），而是「为刷通过率调 Prompt」的自我过拟合。对策三条，都低成本：

1. 用例来源可追溯（5.1 的 fail-fast 是机械保证）；
2. 持续从真实失败补新用例、**退役被刷穿的用例**（报告层的配对差异表就是发现"某条突然从红转绿"的眼睛）；
3. 换模型时整体重跑（`AGENTS.md` 已验证清单已要求）。

---

## 六、报告规格（`scripts/evals_report.py`）

命名避开 `eval_` 前缀（见第九节），纯离线读取器，不调用任何模型。

**输入**：`evals/results/*.json`，按文件名时间戳排序。**只读，不删不轮转**——`evals/results/` 是本地证据沉淀，删除属数据红线。

**输出三段**：

1. **通过率表**：每条用例一行，分列「最低单轮通过率」与「平均通过率」。这是本方案最核心的一列：`pass_rule: all` 语义上就是 pass^k，`min_rate` 是均值阈值折中，两者混成一个数字时「有能力但不稳定」与「能力不足」不可区分（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。数据直接从已有 `runs` 数组算，**零新采集**。
2. **配对差异**：与上一次同名用例逐条比，标 `↑ ↓ =`。不引入 `latest.json`、不改 `run_evals.py`——排序取末两份快照即可。
3. **成本**：每条用例 `completion_tokens` 合计与每轮均值。口径必须显式标注：**这是评测进程口径，不是生产 usage 口径**。项目已有第二套记账通道（`server/llm.py:50` 的 `last_usage`、`server/assistant/adapters/base.py:40` 的 `token_event(..., ttft_ms=...)`、dispatcher 的 `audit_inputs` / `commission_usage`、`evaluator.py:151` 的 `_check_token_budget`），第一步**不接**，避免产出两套互不相等的成本数字；接入留第二轮。

**必须处理的两种边界态**：

- 空目录（新克隆上 `evals/results/` 被 `.gitignore:61` 忽略，目录为空）：打印「无历史数据」并 `exit 0`。
- 单快照：打印「仅一份快照，无配对基线」并 `exit 0`。
- 输出中**不得出现行首 `SKIP`**，否则会被 `run_gates` 的 `skip_regex` 判为跳过（`run_gates.py:445-450,469-474`：SKIP 且非 `--allow-skip` 即非零）。

**跨层比较口径**：offline 运行只落 6 条、`--live` 落 9 条（`run_evals.py:227-254`），live 才有 `turn_meta`、offline 的 `turns` 恒为空（`:203`）。报告必须标注每份快照的 layer 构成，否则用户看到「通过率 100%→67%」无法判断是退化还是 run 数变了。

---

## 七、语音层切片（T4）

已核实的真缺口只有一个：`barge_in → interrupted → playback_stopped` 及「打断后旧轮不得再逃逸 `audio / text_delta / done`」目前只在 `scripts/bench_voice_loop.py` 的墙钟基线里，**没有 CI 断言**。其余事件序已被 `test_speech_performance*.py` / `test_micro_delivery.py` 覆盖，不重造。

做法：`evals/checks.py` 新增 custom check `barge_in_timeline`（约 60–90 行），复用 `scripts/test_speech_performance_route.py:81-97` 已建立的 in-process `Connection` + `Socket` harness（无需 uvicorn、无需 Key），驱动一次 `stream_respond` 后在首个 audio 前发 `barge_in`，断言：`interrupted` 到达、旧轮不再逃逸 `audio/text_delta/done`、turn fence 正确。配一条 `evals/cases/barge-in-timeline-offline.yaml`（约 12 行，`layer: offline`，`pass_rule: all`）。

声网四段里的 `algorithm_processing`（端点判停）与 `asr_ttlw`（ASR 尾词）在 Mock 文本轮里没有对应量、不可离线表达（见 `EVAL_MECHANISM_RESEARCH.md` §2.9.1 的口径注意），归 live 语音路径；真人面板收集必须与聚合器 + 基线锚同时交付，否则退化成 shelfware；ASR 字错率、TTS 自然度自动化都留第二轮，TTS 自然度永不自动化、只作人工 MOS。

---

## 八、散落资产处置（T6）

三分类，两处关键修正：

1. **内联夹具化**：6 个因夹具缺失而 `SkipTest` 的离线契约测试（`test_memory_eval.py`、`test_procedural_memory_eval.py`、`_induction.py`、`_diagnostics.py`、`_source_projection.py`、`_boundaries.py`）。正解**不是**恢复被 `.gitignore:54` 忽略的 `eval/research/*.json`（本地恢复了 CI 仍空跑），而是照两个现成样板改写成自包含：`test_editor_quality_eval.py:54`（注入 `ScriptedModel` + `fixture()` 内联构造）与 `test_procedural_memory_eval_snapshot.py:19-36`（`setUp` 建临时目录）。先做 `test_memory_eval.py`（约 40–80 行，它是 5 层导入链的根、样板效应最大）。
2. **保留为探针**：程序性记忆家族是 5 层导入链（`eval_memory → eval_procedural_memory → _induction → {_diagnostics, _boundaries}`），删中间任一层会断掉后代 import，整体保留并加固离线契约测试，归入探针区。
3. **删除**：`scripts/eval_scenarios.py`（约 121 行）——导入期即 `FileNotFoundError`（`:26` 顶层读 `eval/scenarios.json`）、功能已被 `evals/cases/` + `evals/checks.py` 覆盖、文档指向已漂移（`docs/REQUIREMENTS.md:48`、`docs/COMPANION_ASSISTANT_DESIGN.md:350` 需同步改）。

live-only 三脚本（`eval_draft_grounding.py` / `eval_personalization.py` / `eval_world_intent_live.py`）维持现状，显式归入「探针、不进 CI」，与 `docs/TESTING.md:312` 既有口径对齐。`probe_sm_scenarios.py` 与 `scripts/probe_task_fsm_mutations.py` 会真改 `server/task/` 字节再还原，**永不进 CI**，此风险单独记入 `docs/TESTING.md`。

---

## 九、CI 与门禁接线

- `ci.yaml` **不改**：现有四步（`check_protocol.py` / `run_evals.py` offline / `scripts/test_*.py` 循环 / `npm test`）继续承担门禁轨。
- `run_gates.py` 加一条 `evals_report` gate（约 8–12 行，逐字仿 `:322-327` 的 `evolution_quality_report`），并列入 `ALWAYS_REQUIRED`。语义是「让报告跑得完本身成为一条断言」——报告不进退出码，报告的可执行性进。
- `docs/TESTING.md:312` 的分区判据从「按文件前缀」改成「按是否调用真实模型 / 有固定预算」，`scripts/evals_report.py` 因此不被误判成"验收不要跑"的评测。
- judge 永不进 CI（无 Key）；`--live` 仍是手动触发，且带 Key 实跑时 live 失败计入退出码（`run_evals.py:282`，文档口径已按此修正）。

---

## 十、红线核对

| 红线 | 本方案是否触碰 |
|---|---|
| 改 `server/assistant/executive.py` | 否。全部改动落在 `scripts/`、`evals/`、`docs/` |
| 新增调色板颜色 | 否。不涉及任何前端 |
| 自造 `PROTOCOL.md` 之外的 WS 事件 / API 字段 | 否。judge 的 verdict/reason 只进 stdout 与 `evals/results/*.json`，不经 `Connection.send_turn`；语音层只读既有事件 |
| 重启 8000 / 8010 | 否。全部离线进程；若复用 bench 的 uvicorn 基础设施必须沿用其自起自停端口（`bench_voice_loop.py:48,511-513`） |
| 删数据 | 否。报告层只读，禁止轮转或清理 `evals/results/` |
| 快环内不得阻塞 | 否。judge 与报告都跑在评测进程里，不在 `Orchestrator.respond()` 路径上 |

两条 latent 路径明确不做：报告前端化（加内部路由或 WS 事件即踩协议红线，要走 `PROTOCOL_REQUEST`）；在生产语音链路插计时打点做「首音延迟」（有踩「快环不得阻塞」的风险，且四段口径本身不含 TTS 与 RTC 段）。

---

## 十一、分期与依赖

| 票 | 内容 | 文件 | 新增 | 依赖 | 需 Key | 验收 |
|---|---|---|---|---|---|---|
| **T1** | 报告层 | `scripts/evals_report.py`（新） | 180–240 行 | 无 | 否 | 本机已有快照实跑；造两份快照的临时夹具验配对与空态；`run_gates --only evals,evals_report` 全绿 |
| **T1b** | 报告层接 CI（可选） | `.github/workflows/ci.yaml` | 约 3 行 | T1 | 否 | CI 上单快照态 `exit 0` |
| **T2** | origin fail-fast | `scripts/run_evals.py` | 约 5 行 | 无 | 否 | offline 全绿；临时用例缺 origin 返回 1（验后删） |
| **T3** | 日志漏斗 + 内联夹具样板 | `scripts/mine_eval_candidates.py`、`scripts/test_mine_eval_candidates.py`、`scripts/test_memory_eval.py` | 150–200 + 80–120 行 | T2 | 否 | 自建同 schema 临时 sqlite 验命中/不命中/去重/脱敏/空库；`run_evals` 零回归；`check_protocol` PASS |
| **T4** | barge-in 事件链 | `evals/checks.py`、`evals/cases/barge-in-timeline-offline.yaml` | 60–90 + 12 行 | 无 | 否 | `run_evals` offline 全绿 + `test_core.py` 全绿 |
| **T5** | judge 三态 | `evals/checks.py`、`scripts/run_evals.py`、`evals/cases/hallu-empty-retrieval-live.yaml`、`server/config.py`、`.env.example`、`docs/TESTING.md` | 60–100 + 45–70 + 8 + 6 + 5 + 10 行 | T1 | **是** | `--live` 实跑；judge 缺 Key 时记 review 不记 fail、退出码仍 0 |
| **T6** | 资产处置 | 其余 5 个 `test_*eval*.py`、删 `scripts/eval_scenarios.py`、两处文档指向 | 约 40–80 行 × 5 | T3 | 否 | 各 test 从 `SkipTest` 变真跑；grep 确认 `eval_scenarios` 无 import 方 |
| **T7** | 第二轮可选 | ASR 字错率小集合 / 真人面板 / 成本接 usage 通道 / `latest.json` | 待定 | T1 | 部分 | 待定 |

---

## 十二、验收命令

每张票落地后按序跑，全绿才算完成：

```bash
python scripts/check_protocol.py
.\\.venv\Scripts\python.exe scripts\run_evals.py        # offline 层，黄金命令
python scripts/test_core.py
npm test
.\\.venv\Scripts\python.exe scripts\run_gates.py         # 含新增 evals_report
.\\.venv\Scripts\python.exe scripts\run_evals.py --live  # 仅 T5 起，需 LLM_API_KEY，手动
```

基线（2026-10-24 复跑）：offline 层 6 passed / 0 failed / 0 xfail。任何一票落地后此数不得变差。

---

## 十三、留给第二轮的决定

1. **judge 凭据落点**：`server/config.py` 新增 `llm_judge_*` 四项（仿 `:169-172` 的 `llm_extract_*`，动生产配置面）/ `evals` 侧直读 `os.getenv`（绕过配置层约定）/ 扩展 extract 槽位（同厂商，违背异源要求）。推荐第一种。
2. **报告层是否含 live 采样**（含则要处理 layer 构成与 run 数不同的比较口径）。
3. **成本记账是否接既有 usage 通道**（`last_usage` / `token_event(ttft_ms)` / `commission_usage`）。
4. **ASR 字错率小集合**：固定合成 mp3 语料、版本钉死、按 condition 分报，live-only。
5. **真人面板**：与聚合器、基线锚三件套同时交付。

---

## 十四、Sources

- 本方案的全部外部依据与 URL：`docs/EVAL_MECHANISM_RESEARCH.md`（已于 2026-10-25 按对抗核实修正 4 处事实：MT-Bench 缓解归属、声网 `llm_ftfs` 口径、Sources 两处标题归属、用例条数误写）。
- 本方案的现状事实来自 2026-10-24 的三路对抗核实工作流（外部来源核查 / 代码事实核查 / 覆盖矩阵核查），关键论断均带 `文件:行号`。
- `docs/TESTING.md:42` 的 live 退出码口径已按 `scripts/run_evals.py:282` 修正。

