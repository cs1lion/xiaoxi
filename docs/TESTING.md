# TESTING — 黄金测试命令手册

仓库里有上百个测试脚本，Agent 汇报"我做完了"之前，**必须把本节里属于自己角色的一行命令全部跑通**，并把结果附在汇报里。命令统一用项目虚拟环境（系统 `python` 与 numpy 2.x 不兼容，会直接 ImportError）：

```bash
.\.venv\Scripts\python.exe <script>
```

## 全局门禁（所有角色，任何改动后必跑）

```bash
.\.venv\Scripts\python.exe scripts\check_protocol.py   # 协议漂移 + REST 接缝，必须 PASS
.\.venv\Scripts\python.exe scripts\check_memory_contract.py  # 记忆装配接缝静态契约门（ast；build_context/context_with_status 不得单目标赋值、元数不得漂移），必须 PASS
.\.venv\Scripts\python.exe scripts\test_core.py        # 离线核心基线（passed 数以实跑输出为准），零回归
.\.venv\Scripts\python.exe scripts\run_evals.py        # 回归评测离线层，失败即非零退出码
```

### 门禁报告（一条命令确认"每道门都真的跑过"）

```bash
.\.venv\Scripts\python.exe scripts\run_gates.py                 # 全跑，跳过即失败（道数以输出为准）
.\.venv\Scripts\python.exe scripts\run_gates.py --allow-skip    # 跳过只告警（兼容旧口径）
.\.venv\Scripts\python.exe scripts\run_gates.py --only protocol,core,evals
```

为什么单独立这个命令：若干回归脚本在环境不满足时会打印 `SKIP` 并以 0 退出
（`test-ui-nav-browser.mjs` 在 8000 未起时就是这样；`test-body-coordination-browser.mjs`
等缺本地夹具时同理）。逐条看退出码全是绿的，但那一轮其实什么都没验。
`run_gates.py` 把每条命令的退出码与输出一起收上来，命中 `SKIP` 的门禁单独列一行并
以非零退出——**绿必须来自真跑过，不能来自没跑**。

浏览器门禁需要本地服务在 8000；服务未起时先
`.\.venv\Scripts\python.exe scripts\run.py --port 8000`。

改动 `web/` 前端文件后必须杀掉旧服务进程再重启，否则 `StaticFiles` 缓存的目录
列表会让新增前端文件持续 404，浏览器回归会假失败。

### 门禁外的测试（本地不跑，CI 跑）

`scripts/gates_unregistered.txt` 是一份棘轮基线，列着**不在本地 `run_gates`
里、但会被 CI 的 `for f in scripts/test_*.py` 跑到**的测试脚本。所以「本地
`run_gates` 全绿」不等于「CI 绿」。清单由 `scripts/test_gate_coverage.py
--write` 生成，条数与登记数以该脚本输出为准，不在这里写死。

留在清单外的通常是这几种，都不是疏漏：

- **需要外部条件**：缺本地夹具或真实服务就整组 `skipUnless` 跳过（如
  `test_procedural_memory_*` 系）。`exit=0` 是跳过算通过，登记进门禁后反会被
  「任何门禁跳过即失败」判红。
- **探针/一次性脚本**：写在 `scripts/` 下但不是回归断言。
- **非 unittest 壳**：没有 `def test_`，只是被 glob 到的辅助脚本。
- **他人在途文件**：未提交的新测试，登记它会让别人 checkout 后门禁必红。

要认领其中一个：在 `scripts/run_gates.py` 的 `GATES` 里加一条、从清单删掉、
再跑 `python scripts/test_gate_coverage.py --write` 对齐。**先确认它已
`git ls-files` 跟踪**，且在 `TEST_SENTINEL=1` 下真跑真过——后者是
`run_gates` 给每个门禁子进程的真实环境，普通环境 `exit=0` 推断不了它。

### 环境与机制门禁（2026-10-27 起，全部 ALWAYS_REQUIRED）

2026-10-25 事故的形状是「门禁全绿但 `.venv` 缺整个语音栈」——离线门禁把
ASR/TTS mock 掉，环境残缺它们看不见。此后新增的门禁把「解释器完整」
「tmp 卫生」「lessons 扫描有界」变成断言，均随 `run_gates` 全跑：

```bash
.\.venv\Scripts\python.exe scripts\check_venv_lock.py      # 跑门禁的解释器与 uv.lock 一致（关键子集缺失/不符即红，全集漂移只报数）
.\.venv\Scripts\python.exe scripts\tmp_sweep.py --dry-run --ceiling scripts\tmp_ceiling.txt  # tmp/ 只增不减棘轮（超上限即红）
.\.venv\Scripts\python.exe scripts\test_lessons_cap.py     # #59：lessons 扫描边界来自真实行数（COUNT），超 1000 条不老不退役
```

`venv_lock` 与 `tmp_sweep` 是**本机环境门禁**：CI 用 pip + requirements-ci.txt
（非 uv.lock）且新检出无 tmp/，所以 CI 不跑它们属合理；本地 `run_gates.py`
全跑时二者在 ALWAYS_REQUIRED，跳过即失败。

### 记忆检索等价与快环行为门禁（#31，2026-10-26 起）

```bash
.\.venv\Scripts\python.exe scripts\test_memory_ranking_equivalence.py  # 两阶段检索与「全表建对象再排序」逐条等价（ALWAYS_REQUIRED）
.\.venv\Scripts\python.exe scripts\test_memory_fastloop.py             # 检索不阻塞事件循环（sleep(0) 心跳行为门禁）
```

`memory_ranking` 是确定性等价比对，纯逻辑、`:memory:`、无服务依赖，已入
ALWAYS_REQUIRED。`memory_fastloop` 的判据是墙钟停顿（心跳 + 10ms 阈值），
重载机器上有假红风险（2026-10-27 实测 11.93ms，线程池预热后稳定），因此
随全套回归常规跑但不做「跳过即失败」的硬门；真值以 code review 与
`docs/LATENCY_BASELINE.md` 的实测为准，不以某一刻的机器负载为准。

### 生产库哨兵（默认已开，2026-10-25 起）

`run_gates.py` 给每条门禁子进程注入 `TEST_SENTINEL=1`。测试态的进程一旦打开
仓库 `data/` 下的 SQLite 就**就地报错**，拦在打开点（`MemoryStore.__init__` /
`CommissionStore.__init__`），因为 import 期抛会误伤只读配置的静态检查器。

它曾经默认关着：#39 把隔离做成机械保证后，一开就有 8 个历史套件立刻变红
（`mood_writer`、`cross_day`、`self_evolution`、`config_hot`、
`config_hot_consumers`、`world_bridge`、`evolution_quality_report`、
`memory_manage`）。#41 已把这八个全部钉到临时根，于是它现在**默认打开**。

- **关着的门卫等于没有门卫**：#39 那 379 条测试数据正是从这条通道攒进真实台账的，
  而一个「开了就 8 红」的开关没人会一直开着。
- 调试一个尚未隔离的新套件时可以临时关：`GATE_SENTINEL=off python scripts/run_gates.py`。
  但**合入前必须让它在开着的状态下全绿**——一个新套件如果只有关掉哨兵才能过，
  它就是在往真实库写。
- 隔离手法见 `test_core.py` 与 `test_memory_manage.py`：前者在任何 `from server...`
  之前 `os.environ['DB_PATH'] = ':memory:'`，后者除了 patch 配置根，还加了
  `guarded_connect` 当第二道锁。

> 顺带一条经验：`server/app.py` 的 `_service_data_dir` 曾经拿模块级 `ROOT` 当
> 「仓库根」参照，而那是 `from .config import ROOT` 的绑定——测试「先 patch
> `config.ROOT`、后 import」时它会跟着漂，判据失效，数据根掉进
> `app-test-data-*`。现在用 `Path(__file__)` 算，那是唯一 patch 碰不到的锚。
> 回归见 `test_memory_manage.py` 的 `ServiceDataDirTests`。

## 回归评测（evals/）

幻觉与快环类事故的固化回归，用例在 `evals/cases/*.yaml`，一文件一用例，runner 是 `scripts/run_evals.py`。

- **offline 层**：无网络、无 Key、确定性，记忆库强制 `:memory:`。已纳入上面的全局门禁与 CI，失败即非零退出码。涉及记忆装配 / Prompt / 模型切换的改动必须跑。
- **live 层**：真实模型，需显式 `--live` 且配置 `LLM_API_KEY`；无 Key 或 Mock 模式时自动跳过并明示，**跳过态**退出码为 0（不算失败）；但显式 `--live` 且真实跑起来时，live 用例失败计入 failures、退出码为 1（`scripts/run_evals.py` 的 `main_async` 末尾 `return 1 if failures else 0`，2026-10-24 按代码复核；**行号会漂移，以函数为准**）。换模型或改 Prompt 装配逻辑时手动跑一次，每次运行把每条用例每次的原始回复和判定写入 `evals/results/`（gitignore，只存回复与判定，不存 prompt 与凭据），并打印通过率表。live 的「是否编造」第一版用正则判断，有误判风险，必须抽查落盘的原始回复。

**花钱前的两道闸（R27，2026-10-27 新增）**：本项目 `.env` 常驻真实 Key，所以「忘了加限制直接 `--live`」是条真会花钱的路径。
- `--dry-run`：不调模型、不落快照，只打印预计的 completion 调用数（被测模型 + 裁判分列）与估算 token 数，退出码 0。估算口径写在 `scripts/run_evals.py` 的 `EST_*` 常量里（出处：`evals/results/` 的 `turn_meta` 实测统计，中位数而非拍脑袋），**是估算不是报价**，真实数字以 `evals_report` 的 `completion_tokens` 列为准。
- `--max-calls N`：预计调用数超过 N 即**拒绝开跑**（fail-fast，不跑一半停在半路），并按用例列出超支来源。默认 `None`（不限），不带该参数时行为与以前完全一致。
- 另有一道**只警告不拦截**的线：预计 token 超过 `EST_TOKEN_WARN_THRESHOLD` 时打印显著警告。阈值取全量 live 实测值约 3 倍，让警告保持稀有——正常全量跑不触发，只有异常放大的跑法（临时加大 `runs`、`--case` 漏写导致全量、用例数暴涨）才触发。
- 典型用法：`--dry-run` 看价 → 觉得贵就 `--case` 缩小范围或调 `--max-calls` → 确认后再真跑。
- **live 层的装配口径（重要）**：live 用例走 `build_messages` + `EMOTION_INSTRUCTION` + 真实检索的"装配级"调用（`complete_raw`），**不是**生产全链路 `Orchestrator.respond()`（少了 `_render_turn_context` 动态段、pacing 后处理、历史裁剪）。它只多了一步与生产同款的截断救援：`finish_reason=length` 且正文过短时，用 `reasoning_effort="minimal"` 再要一次短回复；`finish_reason / rescued / completion_tokens` 会随结果落盘。live 结果代表"Prompt 装配与截断救援没坏"，不代表端到端行为；要验全链路需另起浏览器回归。**2026-10-17 复核**：此前记录的"emotion-tag-retention-live 10 次中 3 次缺标签"根因不是多轮指令遗忘，而是推理占满 token 预算（`finish_reason=length`、正文 0 字），修前 2/3 轮通过。`LLM_REPLY_TOKENS` 900→1200、救援改为 minimal 推理、生产空回复兜底后，连续 12 次运行 / 72 轮全绿；这是 R2 抽样，不是稳定性结论。
- **2026-10-20 live 实跑**：`emotion-tag-retention-live` 3/3、`accuracy-over-persona-live` 3/3、`hallu-empty-retrieval-live` 3/3；原始回复与判定按上面的规则落在 `evals/results/`，仍是装配级，不是生产全链路。
- **评测层与生产的刻意差异（2026-10-25）**：生产在 `server/dialogue/orchestrator.py:1091` 有一层无条件空正文兜底（`if not text.strip(): text = '[base]嗯，我在呢。'`，2026-10-25 起带 `[base]` 前缀），保证用户不听到静音；`scripts/run_evals.py:run_live_turns` **刻意不复刻**那层——评测层测的是「模型能不能稳定产出合规回复」，兜掉就等于掩盖退化。length 截断救援两层是相同的（都降到 minimal 推理、600 token）。所以 `emotion-tag-retention-live` 报出的「空回复」是生产里用户永远看不到的模型裸行为——这是有意为之，不是漏抄。（D2 已把生产兜底文案补上 `[base]`：无标签的兜底句自己就违反「每轮回复以合法 [情绪] 开头」这条不变量。）
- **缺 [情绪] 标签为何算硬失败（2026-10-25 定级）**：保持硬失败，理由三条。① `AGENTS.md` 把「每轮 Assistant 示例都带合法 [情绪] 标签」列为不可违反的设计约束，它是显式不变量；② 该用例的存在意义就是抓格式漂移，降级为告警等于放任漂移；③ 「性格与事实准确性冲突时准确性优先」管的是**两者冲突时听谁**，不是"persona 可以不达标"——标签是 persona 的最低格式要求，与准确性从不冲突，拿它当降级理由属于误读该约束。检查本身已区分三种失败（空回复带 `finish_reason` / 无标签前缀 / 标签非法），排障可定位到具体轮次与模式。
 - **ASR 字错率（CER）的口径（2026-10-27 落档，R43）**：`evals/checks.py` 的 `char_error_rate(reference, hypothesis)` 是纯函数，只保证「给定参考与假设文本时算得对」——中文按字符不按词（中文无天然词边界，按词算要先分词、引入分词器差异），编辑距离 / 参考长度，上限不封顶。**ASR 的真实字错率至今没有评测，也不打算用合成音频假装测过**：那需要固定音频语料 + 真实 ASR 后端 + 固定测试环境，属 live-only，且 Mock 模式下的 ASR 被 mock 掉、离线量不到（2026-10-25 事故的形状正是「门禁全绿但语音栈整个缺失」）。接 live 时必须同时报**域与条件**（方言 / 口音 / 噪声 / 远场），否则数字不可比——依据见 `docs/EVAL_MECHANISM_RESEARCH.md` §2.9.3。离线契约测试见 `scripts/test_evals_guards.py` 的 `CharErrorRateTests`。
- **xfail**：`status: xfail` 的用例照常运行并在汇报中列出，但不计入失败（必须写 `reason`）。
- **offline 用例清单（条目数以 `evals/cases/` 目录实况为准，别信这里的数字）**：`fastloop-nonblocking-offline`（快环不阻塞，事件序断言）、`fewshot-emotion-tag-offline`（few-shot 示例带合法情绪标签）、`hallu-empty-retrieval-offline`（空检索必须注入负向约束）、`hard-delete-offline`（SQLite 物理硬删除含 `integrity_check`）、`lesson-injection-offline`（程序性 lesson 检索）、`tier-assembly-offline`（三档置信度装配顺序）、`barge-in-timeline-offline`（T4，2026-10-25 新增：`barge_in` 打断链必须发 `interrupted`→`playback_stopped`，且打断后旧轮迟到的 `audio`/`text_delta`/`done` 一帧不得逃逸；事件序断言不用墙钟，复用 `test_speech_performance_route.py` 的 in-process AST 提取 harness，纯离线零 Key、不起 uvicorn、不占 8000/8010、只读既有 WS 事件）、`valid-time-history-delivery-offline`（时效性历史投递）、`valid-time-future-delivery-offline`（时效性未来投递：未来界计划带 time_note、措辞不得对整条记录断言非现状）、`persona-negative-emotion-boundary-offline`（persona 必须声明「用户要求停止调侃时立刻停止」的边界；**这是配置层守卫、不是行为验证**——它只证明约束写在 persona 里，真实日志中「用户说别调侃了、Agent 仍继续调侃」的违反样本需 live 跑才能抓，配置层管不住。用例红即表示有人把这条状态从 persona 删掉或改坏了）、`persona-voice-style-offline`（persona 必须自述简短口语语音风格与去文字框化，且状态机取值仍合法——两条都是配置层守卫，不证明模型照做）。

  数条数用 `grep -l "^layer: offline" evals/cases/*.yaml | wc -l`，别手写——这条清单的标题数字与条目数都会随新增用例漂移（HEAD 上是「2026-10-25 实况 7 条」且只列 7 个用例，而目录实际已有 11 条 offline），原因是每加一条用例只补了条目、忘了改标题。

- **judge 三态（T5，2026-10-25）**：live 层可挂异源 LLM 裁判（`evals.checks:judge_hallucination`，`check.type: custom`，函数返回三元组）。判定分三态 `pass` / `fail` / `review`：正则与裁判**一致才定论**；裁判未配置、调用失败、输出无法解析、自报 unclear、或与正则不一致，一律 `review`——既不计通过也不计失败，交人工复核，只有 `fail` 计入退出码。裁判槽位是 `llm_judge_provider / llm_judge_base_url / llm_judge_api_key / llm_judge_model`，**设计上必须与被测模型不同源**（同源自评有自我偏好偏差）；留空即不启用，该 check 整层退化成「全 review」，不会把无 Key 的跑态假装成判过。**当前偏离（用户 2026-10-25 决定）**：judge 用同一供应商的另一模型（`step-5-preview` 判 `step-3.7-flash`），槽位独立但并非不同源——后果是 judge 永不单独把用例判红（`evals/checks.py` 的 judge 段注释写明了这条及其原因），换模型或改判定逻辑时须连同那条注释一起复核。judge 永不进 CI（CI 无 Key）。去偏手段只用论文本身支持的（交换顺序两跑取一致、judge 先给思维链），归属见 `docs/EVAL_MECHANISM_RESEARCH.md` §2.5。离线契约测试见 `scripts/test_judge_three_state.py`，覆盖五类：三态判定路径、`run_case` 端到端（含裁判客户端构造路径回归）、真实 HTTP 往返、退出码分桶、`main_async --live` 端到端；后四类全部零凭据零外网，`RESULTS_DIR` 指向临时目录，不写 `evals/results/`。**条数以实跑输出为准**（`.\\.venv\\Scripts\\python.exe -B scripts\\test_judge_three_state.py`），本文不写死——它随用例增减变化，写死就会变成三个互相矛盾的旧数字。

### 写动作崩溃矩阵（2026-10-20）

`scripts/probe_outbox_crash.py` 用两个子进程分别在“副作用后、回执前”和“回执已落盘、事项投影前”硬退出，再由父进程走真实 `AssistantService` 启动路径验收：前者转 `unknown` 并阻断 resume，后者只补投一次且不重复副作用。它不碰生产数据，已纳入 `run_gates`。

### 退役/不在门禁内的评测夹具（2026-10-20；2026-10-25、2026-10-26 修订）

`scripts/` 下这批程序性记忆评测依赖曾随 `/eval` 清理被删、未入 git 的本地夹具，**不在 `run_gates.py` 里**。2026-10-25 已把「整组 `SkipTest`」改成「逐条 / 逐类 `skipUnless`」：自包含用例真跑，依赖夹具的用例按名跳过，跳过理由写在装饰器上——不再让整组在 CI 里假装通过。

| 文件 | 真跑 | 按理由跳过 | 缺什么 |
|---|---|---|---|
| `test_procedural_memory_source_projection.py` | 9 | 1 | `eval/research/procedural_memory_eval_cases.json` |
| `test_procedural_memory_boundaries.py` | 5 | 6 | `eval/research/procedural_memory_boundary_cases_v1.json` |
| `test_procedural_memory_diagnostics.py` | 8 | 7 | `eval/research/procedural_memory_diagnostic_inputs_v1.json` |
| `test_procedural_memory_eval.py` | 4 | 20 | `procedural_memory_eval_cases.json` + 配套 oracles |
| `test_procedural_memory_induction.py` | 8 | 0 | 无——H4 已用 `scripts/pm_induction_fixture.py` 内联构造夹具（3 bundle × 2 recipe，形状由 `load_sources()` 与 `domain.prepare()` 的校验钉死），不再依赖磁盘文件；该文件曾被并行会话回退成整组 SkipTest，2026-10-26 已重新接上 |

条数为 2026-10-26 本地实跑值，会随用例增减变化，**以运行输出为准**。补夹具后跳过会自动转真跑；引用这些层的历史结果时仍须标明"夹具缺失、未复跑"。**不要把 `run_gates` 全绿解释成这些评测也跑过**——它们不在 `run_gates.py` 里，只在 `ci.yaml` 的 `scripts/test_*.py` glob 中。

**这些夹具为什么不能合成（2026-10-26 查证，防后人走错路）**：剩下的 34 条跳过**不要**靠造合成夹具消掉。分两类，两类都会变成假覆盖：

- **eval 的 20 条**：`load_specs()` 要一份固定规格（3 sources / 6 targets / groups 恰为 `N,E,P,G` / 预算常量精确匹配），而 `gold_material()` 读的是 `source_oracle['human_card']`——**金标准**。合成它等于让评测拿我编造的答案自证，比诚实跳过更糟。
- **boundaries 的 6 条与 diagnostics 的 7 条**：断言的是具名边界条件（如 `boundary-truncated`、`boundary-concurrent-ambiguous`、`boundary-missing-confirm`）。合成数据即便结构合法，也**不保证真的触发名字所示的那条边界**——测试会跑，但测的不是名字承诺的东西，同样是假覆盖。

正解只有两条：拿到真实的域夹具，或像 H4 对 induction 那样**由代码自身校验反推出形状**（induction 能这么做，是因为 `load_sources()` 与 `domain.prepare()` 的约束足以唯一确定夹具形状；eval/boundaries/diagnostics 不满足这个条件）。所以补齐之前，跳过是诚实的。

**2026-10-25 变更**：`scripts/test_memory_eval.py` 已改为内联合成夹具（`build_cases()` 内存构造 12 条，仍经真实 `load_cases` 逐条校验），不再 `SkipTest`；`ci.yaml` 的 `scripts/test_*.py` 循环会真跑它（本地实跑 19 条 OK）。它是其余程序性记忆测试内联化的样板——正解不是恢复被 `.gitignore:54` 忽略的 `eval/research/*.json`（本地恢复了 CI 仍空跑），而是让夹具随测试一起分发。
- **新增用例**：每次出幻觉事故就加一条 YAML。字段见 `evals/cases/` 现例；`checks` 支持 `contains / not_contains / regex_any / not_regex / custom`，custom 判定函数写在 `evals/checks.py`，用例里以 `evals.checks:<函数名>` 引用。预置状态写 `setup.memories`，用户轮次写 `turns`，live 默认跑 `runs: 3` 次，通过线由 `pass_rule: all | min_rate:0.8` 控制。`origin` 必填（来源实验 / 事故 / commit）；缺 `origin` 或空串会在 `main_async` 起手处 fail-fast 并非零退出（`scripts/run_evals.py`）。
- **候选漏斗**：`scripts/mine_eval_candidates.py` 以 `mode=ro` 只读 `data/memory.db`（不 import 任何 server 模块、不写活库），按四类确定性启发式（共指/省略追问、疑似幻觉面、事实挑战、负面情绪边界）提名评测候选，凭据与个人信息脱敏后落 `eval/research/case_candidates_*.json`（`.gitignore:54` 覆盖，**不进版本库**）；候选经人工筛选与改写后才手写进 `evals/cases/`，脚本本身不写受版本控制的目录。库不存在时打印说明并 exit 0（正常态）。

### 评测报告层（2026-10-25）

`scripts/evals_report.py` 读 `evals/results/` 的运行快照，出八节：通过率表（**平均通过率**与**全过概率 pass^k** 分列）、数据损坏（字段类型不符时）、失败明细、review 明细、与上次快照的配对差异、**趋势视图**、`completion_tokens` 成本，以及**跨快照 review 队列**。它属于**报告层**：只读快照、不删不轮转、不计入任何退出码；「报告跑得完」由 `run_gates` 的 `evals_report` 门禁断言。空目录 / 单快照 / 快照损坏都是 exit 0 的正常态，输出不得出现行首 `SKIP`（否则会被判为跳过）。成本口径是评测进程口径，与生产 usage 记账是两套，暂不合并。

**趋势视图（2026-10-26 新增）**：按**用例自己**最近 `TREND_WINDOW` 次**非 xfail** 出现聚合通过序列（`●` 全过 / `○` 有失败），并给出五标签判定：数据不足（<3 点）/ 长期稳定 / 持续失效 / 最近退化（首真末假）/ 一直抖（其余混合形态）。按用例取窗而非按文件取窗，是因为 offline 与 live 的运行频率差一个量级——按文件取窗会让 live 用例在窗口里一次都没出现、整节空转。它与配对差异的分工：配对差异只说最新一份比上一份好了还是坏了，趋势序列才能区分「一直抖」「最近退化」「长期稳定」，三者处置完全不同（抖 → 加 runs；退化 → 查回归；稳定 → live 用例反而要查断言够不够狠）。全史的失效日期分布与刷穿检测归 `evals_health.py`。字段类型损坏（`total_runs="abc"` 等）时该条进「数据损坏」节并跳过，不让门禁变红——报告层的契约是永不因内容失败。

**两节 review 视图（2026-10-27 补齐文档）**：`review` 的口径是「判定器说不清（judge 未配置 / 调用失败 / 与正则不一致）」，**既不计通过也不计失败、退出码不看它**，需要人工复核。两节分工不同：
- **review 明细**：只看**最新快照**，回答「这次跑得怎么样」——最新快照里有 review 轮次时才出现；
- **跨快照 review 队列**：扫最近 `REVIEW_QUEUE_WINDOW` 份快照，回答「有什么事欠着」——因为最新快照几乎总是纯 offline 跑（live 要手动 `--live`），只看最新快照会让欠账完全不可见。

队列窗口比 `TREND_WINDOW` 大得多（数值以脚本常量为准）：review 只在 live 用例上产生，而 live 手动跑、频率低，窗口小了队列会长期空转，欠账看起来像不存在。

**人工复核怎么做**：打开队列给出的快照文件，找 `runs[].verdict == "review"` 的轮次，读 `turns[].reply`——要判断的是**回复本身**，不是判定结果。复核完若要消掉这一项，需重跑该 live 用例让它转绿或转红。

### 评测健康度层（2026-10-26）

`scripts/evals_health.py` 横向扫 `evals/results/` 的**全史**快照（报告层只比对末两份），回答四个报告层不回答的问题：① offline 层是否还确定（近期窗口内失效即非确定性 bug）；② live 用例是一直抖还是最近退化（每条通过率 + 失效日期分布）；③ 快照数据本身的质量（损坏 / 缺字段 / xfail 占比 / 与 `evals/cases/` 的用例差集）；④ 评测体系自己是否在过拟合（刷穿嫌疑、从未失效的 live 用例、重复/冗余用例——**只标记不判定**，不自动退役任何用例，退役是用户决策）。近期窗口 `RECENT_WINDOW_DAYS=7`（按自然日不按份数：抗单日快照突发，并把建制日的历史欠账排除在门外，只进报告不判红）。

退出码语义：**仅当近期窗口内 offline 用例失效才 exit 1**；live 抖动、历史欠账、数据质量提示、空目录、快照全损坏一律 exit 0，只在输出里标注。`xfail` 用例照常跑但不计入失败（`run_evals.py` 的口径），因此也不进通过率与门禁判定，只进占比统计。与报告层一样只读快照、不删不轮转、输出不得出现行首 `SKIP`（否则会被判为跳过）。`layer` 既非 offline 也非 live 的快照记录进「数据质量」告警，不静默计入任何统计。

**已落地（2026-10-26 核）**：`scripts/evals_health.py` 已落盘，`run_gates` 的 `GATES` 有 `evals_health` 条目且已进 `ALWAYS_REQUIRED`，与 `evals_report` 同级。复跑 `.\\.venv\\Scripts\\python.exe scripts\\run_gates.py --only evals,evals_report,evals_health` 应见三条 PASS；若 `evals_health` 报「未知门禁」即接线丢失。三处（脚本、门禁登记、`ALWAYS_REQUIRED`）必须同进——只加脚本不进门禁会让 `gate_coverage` 红，只进门禁不进 `ALWAYS_REQUIRED` 则它可能被静默跳过。

## V3 内阁总管（全员适用，2026-10-15 起）

```bash
.\.venv\Scripts\python.exe scripts\test_executive_v3.py         # 督办台账 / 观测网关 / 自进化初审 / 台前意图 + ui_nav 领航载荷
.\.venv\Scripts\python.exe scripts\test_agent_adapter_ext.py    # Worker 适配器可插拔（Mock 适配器跑通全程）
.\.venv\Scripts\python.exe scripts\test_verify.py               # server/verify 三种确定性 check 正反例 + acceptance 桥接三态渲染（M2）
.\.venv\Scripts\python.exe scripts\test_stalled_running.py      # M5 缺口复现：重启收口机制通路 + 会话内僵单无人收（第二段钉现状，主体落地后照 docstring 口径翻转）
.\.venv\Scripts\python.exe scripts\test_codex_worker_shim.py   # Codex shim 五种事件 + 真实 artifact ref（假 Codex，不联网）
node web\executive-state.test.mjs                               # 心流防打扰纯逻辑（零 DOM）
node web\ui_nav.test.mjs                                         # 界面自动领航（ui_nav）纯逻辑 + 心流护栏（零 DOM，无服务依赖）
node scripts\test-ui-nav-browser.mjs                             # 领航浏览器回归：抽屉自动开合 / 聚焦 / 子动作 / 侧栏·星图落点（需先启动本地服务；服务未起时自动 SKIP，不算失败）
```

> **改动 `web/` 前端文件后**：必须杀掉旧服务进程再重启（`taskkill //F //PID <持 8000 端口的进程>`
> 然后 `.\.venv\Scripts\python.exe scripts\run.py --port 8000`），否则 `StaticFiles` 缓存的目录
> 列表会让新增的前端文件（如 `web/ui_nav.js`）持续 404，浏览器回归会假失败。

`check_protocol.py` 在 V3 起多管两类：**服务端自用路由**（`/internal/telemetry_proxy`
与 `/internal/durable/*` 只读宿主接口）与 **ui_nav 领航取值域**（协议§二十四，前后端取值不漂移）。
前端不引用前者，所以第五类检查看不见；改了路径会让 Worker 的调用静默 404，
因此显式列在 `INTERNAL_ROUTES` 里，契约一改静态检查直接报警。

跑 V3 相关改动时，上面五行加全局门禁两行全部跑通才算完。

重启收口（服务重启后把半路的督办转成 BLOCKED，让它们重新有出路）：

```bash
.\.venv\Scripts\python.exe scripts\test_restart_recovery.py   # briefing/running/auditing 收口 + drafting 重派 + 停车场不占槽
.\.venv\Scripts\python.exe scripts\test_durable_adapter.py    # durable 适配器：假 sidecar 验密件/事件映射/内核不可用降级/重启对账
.\.venv\Scripts\python.exe scripts\test_durable_supervisor.py # sidecar 监督：起得来/探得活/收得干净（需本机 node，缺则 SKIP）
.\.venv\Scripts\python.exe scripts\test_durable_app_wiring.py # app 接线：环境变量启用、switch_agents、端点提供者、写盘根（需 node，缺则 SKIP）
.\.venv\Scripts\python.exe scripts\test_durable_reconcile.py   # 周期对账：完成采纳/丢失或失败转 blocked/仍跑不误杀/不自动 done
.\.venv\Scripts\python.exe scripts\test_durable_soak.py        # 短时 chaos：多单硬杀 sidecar，重启后恰好一次进度/artifact
.\.venv\Scripts\python.exe scripts\soak_durable.py --duration-hours 24 --concurrency 3 --interval 5 --kill-every 1800 # 24h soak，单独挂机跑；JSON 报告落 tmp/
.\.venv\Scripts\python.exe scripts\test_storage_archive.py     # 存储：在线快照/锁存在拒绝轮转/离线 --yes 轮转
.\.venv\Scripts\python.exe scripts\storage_doctor.py           # 只读报告 durable 活库/归档/备份体积（不打开活库）
.\.venv\Scripts\python.exe scripts\test_durable_read_api.py   # C1 只读宿主接口：鉴权/记忆分档/便笺/事项/Worker 目录
.\.venv\Scripts\python.exe scripts\test_durable_orchestrator_api.py # C2/C2.1：lease/dry-run/确认号/幂等/越权/blocked_kind/重启孤儿
.\.venv\Scripts\python.exe scripts\test_durable_c3_api.py         # C3：review/propose 只读、宿主授权 commit、L1 阻断、reject 判例、工作台入口
.\.venv\Scripts\python.exe scripts\test_durable_clarification_cap.py # 脚本化 BlockingAdapter：adapter→blocked_kind→第 4 次澄清 429
.\.venv\Scripts\python.exe scripts\test_durable_authorization_ui.py  # C4 工作台授权：共享状态/确认单/按调用放行/提议提交/同源拒绝/503 降级
.\.venv\Scripts\python.exe scripts\test_world_bridge.py        # S5 世界桥：begin/delegate、world 工具、路由差分、后台并发/取消
.\.venv\Scripts\python.exe scripts\probe_world_live.py         # S5 真实模型选择探测（world.dispatch/status + 确认门；临时库，产生 API 费用）
.\.venv\Scripts\python.exe scripts\probe_c3_live.py --runs 1      # 真实模型 live 探针：临时库，不碰 data/；会产生 API 费用
.\.venv\Scripts\python.exe scripts\probe_dsh_workspace.py --tasks 3 --keep # 本地 DSH 新工作区 3 个简单任务；会产生 API 费用
cd agent-runtime; node --env-file=..\.env --experimental-strip-types probe-compaction-real.ts # 真实模型压缩样本（32k/8k 生产档，临时库）；会产生 API 费用
.\.venv\Scripts\python.exe scripts\doctor_integration.py      # 接入体检：adapter/executable/args/workspace/MCP/skills，不联网
.\.venv\Scripts\python.exe -B scripts\test_gateway_env.py     # 网关上游默认落在 .env，不默认依赖 15721
```

以上四条已在 `run_gates.py` 里，`python scripts/run_gates.py` 会一并跑并区分"真跑过"与"跳过"。

## 慢环执行内核（agent-runtime，Phase 0–2 + 能力层）

```bash
cd agent-runtime
npm install --ignore-scripts
node --experimental-strip-types selftest.ts                   # 硬崩溃后续跑 + 重复投递幂等
node --experimental-strip-types test-compaction.ts            # 压缩阈值：窗口/保留区/切点一致性
node --experimental-strip-types test-compaction-live.ts       # 真实多轮触发自动 pi.compaction + 摘要请求
node --experimental-strip-types test-skills.ts                # Skills：渐进披露、read_skill、路径逃逸
node --experimental-strip-types test-read-file.ts             # read_file：工作目录内回读、逃逸拒绝
node --experimental-strip-types test-list-files.ts            # list_files：有界目录列举
node --experimental-strip-types test-host-tools.ts            # C1 只读工具：记忆/便笺/事项/Worker + token
node --experimental-strip-types test-profiles.ts              # Worker / Orchestrator 工具面隔离
node --experimental-strip-types test-orchestrator-tools.ts    # C2 工具：lease 来自 RunMeta、模型不能造额度
node --experimental-strip-types test-orchestrator-run.ts      # Orchestrator 端到端：模型调 dispatch_task → 最终回答
node --experimental-strip-types test-block-kind.ts            # block_and_ask 的 clarification/approval 结构字段
node --experimental-strip-types test-mcp.ts                   # MCP：工具包装、readOnly 重放、审批登记
node --experimental-strip-types test-mcp-live.ts              # 真实 stdio MCP fixture：握手/tools/list/tools/call
node --experimental-strip-types test-model-run.ts             # 模型驱动：artifact + usage + 密件/记忆/验收落在 instructions
node --experimental-strip-types test-nudge.ts                 # 只答散文不交差时，催一次能救回来
node --experimental-strip-types test-write-file.ts            # write_file：目录内放行、逃逸全拒、模型驱动真写出文件
node --experimental-strip-types test-handin.ts                # 交差时 ref 规范化：双反斜杠还原、没写过的文件拒收
node --experimental-strip-types test-approval-chain.ts        # 审批全链：拦下→登记→按调用放行→不跨单泄漏
node --experimental-strip-types test-approval-per-call.ts     # 审批绑定参数哈希、一次性消费、同工具不同参数仍需批准
node --experimental-strip-types test-tier-model.ts            # cost_tier → 模型映射（含 conversation 上真选中）
node --experimental-strip-types test-takeover.ts              # 接管本地执行体：密件/事件/重做幂等/stderr 诊断
node --experimental-strip-types test-takeover-recovery.ts     # 接管中硬崩溃：重开不重复拉起执行体
node --experimental-strip-types test-revive.ts                 # 真硬杀后重开：锁的重试预算必须长于过期窗口
node --experimental-strip-types test-background.ts            # 后台任务：每步 checkpoint、不挡空闲等待
node --experimental-strip-types test-approval.ts              # 审批门：未批准阻断且对模型可见，批准后放行
node --experimental-strip-types test-live-tools.ts            # 真实端点是否透传 tools（无 LLM_API_KEY 则 SKIP）
node --experimental-strip-types test-openai-tools.ts          # 自定义 OpenAI 兼容端点的流式/中文/工具调用/usage
node --experimental-strip-types reference/24-child-tasks.ts   # 官方示例：failFast / abort 级联 / 重启续跑
```

> durable 的 SQLite 会随 transcript 一直增长（库本身不删 entry）。季度/按月做冷归档：
> `.\.venv\Scripts\python.exe scripts\archive_durable.py`（在线备份到
> `data/durable/archive/`，不动活库）；只有显式加 `--prune-days N` 才会删除旧档。
>
> `XIAOXI_MAX_TOKENS`（默认 8000）是慢环的 completion 预算，**不要**用 `.env` 的
> `LLM_MAX_TOKENS`（那是快环短回复的 400）：推理型模型会把 1200 全烧在 reasoning 上，
> 返回 `finish_reason:"length"`、正文空、无工具调用，整单以"执行体没交差"失败，
> 而失败信息里 `said` 也是空的，看不出是预算问题。

## Role 1（记忆 / 日记 / 自进化 / 星图底层）

```bash
.\.venv\Scripts\python.exe scripts\test_memory_api.py            # 记忆 REST 契约
.\.venv\Scripts\python.exe scripts\test_memory_orchestrator.py   # 记忆 × 对话编排
.\.venv\Scripts\python.exe scripts\test_memory_store_contract.py # 存储层契约
.\.venv\Scripts\python.exe scripts\test_memory_evidence.py       # 证据链
.\.venv\Scripts\python.exe scripts\test_memory_retrieval.py      # 检索
.\.venv\Scripts\python.exe scripts\test_memory_self_evolution.py # 效用反馈 + 程序性经历（MemRL / EvolveR 机制，见 MEMORY_SELF_EVOLUTION.md）
.\.venv\Scripts\python.exe scripts\test_memory_preference.py     # 偏好合同
.\.venv\Scripts\python.exe scripts\test_agent_loop.py            # assistant/loop.py
.\.venv\Scripts\python.exe scripts\test_task_planning.py        # 结构化任务计划（协议 §25）：有界/验收/恢复/修订
.\.venv\Scripts\python.exe scripts\test_episodic_diary.py        # 第一阶段新建：日记/偏置/火花/星图
.\.venv\Scripts\python.exe scripts\test_cross_day_companion.py   # 跨天陪伴三天验收：Day1 共建 / Day2 恢复 / Day3 续做与修正
.\.venv\Scripts\python.exe scripts\test_self_evolution_quality.py # 自进化质量门禁：新颖度/可追溯/反空洞/反近义繁殖/纵向积累
.\.venv\Scripts\python.exe scripts\eval_self_evolution_quality.py # 自进化质量评测报告（按需，非门禁）
```

`test_cross_day_companion.py` 验的是**管线与状态机**：自然语言「接着昨天的继续」走对路、
事项跨重启恢复、决定/问题/进展在口述里可区分、修订产生版本血缘而非平行决定、
旧决定永不被当作当前决定复述、稿子版本不悄悄切换。它**不**证明真实模型的语义能力
（resolver 是脚本化的）——真实 LLM 的输出质量需另做离线评测，与
`docs/EVOLUTION_QUALITY_EVAL.md` 同类的「机制通过 ≠ 语义合格」声明。

`test_self_evolution_quality.py` 把六个质量维度钉成断言，重点是**反例**：近义复述、
空洞泛化、模板派生理由、编造来源，都必须被判负且种子不得被消化。它证明闸门在工作，
不证明思想质量合格。`eval_self_evolution_quality.py` 出人类可复查的评测报告
（含纵向统计），`--live` 尚未实装。

数据库结构有变更时，改动前后各跑一次：

```bash
.\.venv\Scripts\python.exe scripts\backup_local_databases.py
```

人可审计的记忆快照（只读投影，SQLite 仍是唯一真相源；`--out` 省略则打屏，`--subject user` 只导一个 subject）：

```bash
.\.venv\Scripts\python.exe scripts\export_memory_snapshot.py --out tmp\memory-snapshot.md
```

## Role 2（长语音 / 微句流 / 演出编排）

```bash
.\.venv\Scripts\python.exe scripts\test_voice_turns.py           # 轮次协议（turn_id/打断/取消）
.\.venv\Scripts\python.exe scripts\test_reply_delivery.py        # 固定回复交付器
.\.venv\Scripts\python.exe scripts\test_speech_performance.py    # 动作意图与锚点
.\.venv\Scripts\python.exe scripts\test_speech_performance_route.py
.\.venv\Scripts\python.exe scripts\test_stream_delivery.py       # 流式交付
.\.venv\Scripts\python.exe scripts\test_micro_delivery.py        # 第一阶段新建：微句/预加载/打断（假 TTS，不碰真实供应商）
.\.venv\Scripts\python.exe scripts\test_ambient_tiers.py         # 三梯度分级 / 退避阈值纯逻辑
.\.venv\Scripts\python.exe scripts\test_ambient_orchestration.py # 三梯度编排：破冰兜底 / 首次阈值 / 退避 / 真声
node scripts\test-speech-performance-browser.mjs                 # 浏览器回归：受控 WS + 生成 WAV
node scripts\test-ambient-browser.mjs                            # 三梯度运行链路：micro_act / mumble 气泡 / 接话
npm run test:voice                                                # 语音模块 + 播放 + 双工 + 捕获 + 检查点
```

## 陪伴心流三态胶囊（打磨轮 2026-10-16）

主动开口疏通 + 双 Switch → 三态胶囊重构的回归命令：

```bash
.\.venv\Scripts\python.exe scripts\test_ambient_tiers.py          # 首次阈值 28s / voice_backoff 指数退避
.\.venv\Scripts\python.exe scripts\test_ambient_orchestration.py  # 冷启动破冰 / 梯度3 陪伴接管 / 授权门
node scripts\test-presence-capsule-browser.mjs                   # 三态循环 / 配置派发 / REST 持久化回滚 / 心跳门控
node scripts\test-ui-browser.mjs                                 # UI 契约（含胶囊焦点与失败回滚）
node scripts\test-voice-browser.mjs                              # 语音轮次（配置推送改由胶囊触发）
```

## 动态积木 · Voice-First 交互范式（协议 §23，打磨轮 2026-10-18）

小溪默认交互升级为"简短口语语音 + 动态生成式 UI 积木"（去文字框化）。回归命令：

```bash
.\.venv\Scripts\python.exe scripts\check_protocol.py             # 七类漂移（含积木 kind 三方认账）
.\.venv\Scripts\python.exe scripts\test_aqi_blocks.py            # 流式标签过滤 / 组装器 / 路由直出 / 分流 / 七种 kind 契约（49 例）
.\.venv\Scripts\python.exe scripts\test_voice_turns.py           # 轮次协议（aqi_action 入口 / 星图摘录 / §23.6 主动随路积木）
.\.venv\Scripts\python.exe scripts\test_stream_delivery.py       # 流式交付（积木先于 done 下发）
npm run test:blocks                                              # 积木纯函数：parseCmd 白名单 / 信封校验 / kind 与渲染器一一对应
node web\aqi-flow.test.mjs                                       # 心流防打扰：打字期间挂起、报数、停手后原序补弹（零 DOM）
node scripts\test-aqi-blocks-browser.mjs                         # 7 种积木 DOM / 五类 cmd / nav+drawer_ops / gaze / 上限 / XSS
node scripts\test-ui-browser.mjs                                 # UI 契约（去文字框化：微光接手，无 bot 气泡）
node scripts\test-voice-browser.mjs                              # 语音轮次零回归
```

## Role 3（星图前端 / 工作台）

```bash
node web\starmap\test_starmap.mjs        # 第一阶段新建：数据桥接/检索去抖/narrate 状态机/空态
npm run test:voice                       # 播放链路零回归
node scripts\test-workspace-browser.mjs  # 主工作台零回归
node scripts\test-ui-browser.mjs         # UI 契约零回归
node --experimental-default-type=module --test web\avatar\test_stage_camera.js  # 双态镜头与默认表情（零 DOM）
```

星图手帐视觉层（`web/almanac.css`）改动后，除了上面四行，还要跑：

```bash
BASE_URL=http://127.0.0.1:8010/ node scripts\test-ui-browser.mjs        # 加载新样式后的 UI 契约
node scripts\test-companion-browser.mjs                                  # 共同事项纸上便签
node scripts\test-presence-capsule-browser.mjs                           # 三态胶囊落在胶囊栏
node scripts\test-aqi-blocks-browser.mjs                                 # 积木卡墨色小卡
BASE_URL=http://127.0.0.1:8010/ node scripts\test-ui-nav-browser.mjs    # 领航回归
node scripts\test-voice-browser.mjs                                      # 语音轮次（设置面板不得遮住输入区）
```

> 设置面板的 `max-height` 必须给输入区留出空间：实测 1440×1000 与 390×844 下
> 面板底沿与 `.composer` 顶沿之间要保持 15px 以上，否则 `#sendBtn` 被
> `.settings-section` 拦截点击，语音回归会超时。

浏览器手验（桌面 + 移动视口）不属于自动测试，但验收标准里要求：星图非空白、节点可点、讲述可播放可打断，附截图。

## 真机 Worker 与本地网关（2026-10-17 夜）

本地网关 `127.0.0.1:15722` 负责把 Anthropic Messages 翻译到 `15721` 的 OpenAI 出口；
`scripts/run.py` 会先拉起网关再起主应用。离线契约：

```bash
.\.venv\Scripts\python.exe scripts\test_anthropic_gateway.py      # 72 项：Anthropic/Responses/Chat、路由、fallback、脱敏
.\.venv\Scripts\python.exe scripts\test_claude_worker_shim.py     # 16 项：五种事件 + artifact
.\.venv\Scripts\python.exe scripts\test_workbuddy_worker_shim.py  # 34 项：工具、失败、隔离
.\.venv\Scripts\python.exe scripts\test_dsh_worker_shim.py        # 43 项：多步 token、失败、超时
.\.venv\Scripts\python.exe scripts\test_acceptance_dispatch.py    # 52 项：验收条件 + 有界重试 + 反馈回写
```

## 语音交付（2026-10-18）

供应商在每个音频块前后各垫约 0.36s / 0.38s 静音，与 `pacing.yaml` 设计的呼吸停顿
叠加后，实际句间空隙达 0.9~1.1s。`tts_trim_silence` 按 WAV 修剪首尾静音，
让设计的 pause 成为真实停顿（实测固定开销 0.879s → 0.355s）。

```bash
.\.venv\Scripts\python.exe scripts\test_tts_trim_silence.py      # 11 项：修剪、边缘保留、容器不变、缓存键分格式
```

关掉修剪：`TTS_TRIM_SILENCE=false`。阈值与保留边缘由 `TTS_TRIM_THRESHOLD`
（默认 0.012）与 `TTS_TRIM_PAD_MS`（默认 60）控制。

真实调用会花 token，按需手动跑，不进门禁：

```bash
# 一条命令起网关 + 主应用（已运行则不会重复起）
.\.venv\Scripts\python.exe scripts\run.py

# 单 Worker 真机
.\.venv\Scripts\python.exe scripts\probe_claude_worker.py
.\.venv\Scripts\python.exe scripts\probe_real_worker.py          # Codex，danger-full-access
.\.venv\Scripts\python.exe scripts\probe_roster_claude.py        # 生产花名册选将

# 多 Worker 同题套件（先 --dry-run 看计划）
.\.venv\Scripts\python.exe scripts\probe_live_workers.py --workers claude,workbuddy,dsh --cases note --repeat 1

# durable 接管本地 Agent：派单 → 持续监督 → 验收 → 中断恢复（四段全跑）
.\.venv\Scripts\python.exe scripts\probe_takeover_production.py
.\.venv\Scripts\python.exe scripts\probe_takeover_production.py --dry-run
```

DSH 在本机默认沙箱拒绝写工作目录；花名册/profile 必须显式设
`DSH_PERMISSION_MODE=danger-full-access`，否则会以“sandbox denied write access”阻塞，
这是真实失败，不是可忽略的噪声。

## 不属于回归的脚本（按需手动，别塞进验收）

分区判据是**行为，不是文件名前缀**：会调用真实模型/供应商或有固定预算的脚本（`scripts/eval_*.py`、`scripts/probe_*.py`）属于探针，默认干跑（0 次真实调用），`--live` 需显式配置和预算，验收时不要跑；纯离线读取器（如 `scripts/evals_report.py`）属于报告层，进门禁但只断言"跑得完"。三区（门禁 / 报告 / 探针）完整定义见 `docs/EVAL_MECHANISM_PLAN.md` §三。

## 回归失败时

不允许"已知失败"式汇报。先判断是不是自己改出来的：`git stash` 自己的改动重跑对照（由 Role 0 操作），确认基线后修到全绿再汇报。
