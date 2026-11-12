# TESTING — 黄金测试命令手册

仓库里有上百个测试脚本，Agent 汇报"我做完了"之前，**必须把本节里属于自己角色的一行命令全部跑通**，并把结果附在汇报里。命令统一用项目虚拟环境（2026-10-27 实测订正：系统 anaconda `python` 的 numpy 2.5.1 与同环境 scipy ABI 不匹配，`test_core.py` 一 import 就 `ImportError: numpy.core.multiarray failed to import`；但并非所有脚本都崩——`check_protocol.py` 不碰 scipy，用裸 python 能跑过。所以别按"版本号不对"去换 numpy，用 `.venv` 才是正解）：

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

### 真环境验收（真实 Key / 麦克风由人提供）

```bash
.\.venv\Scripts\python.exe scripts\live_acceptance.py --doctor          # 无 Key 也能跑，体检脚本本身
.\.venv\Scripts\python.exe scripts\test_live_acceptance.py              # 向导自身门禁（离线，exit 0 = PASS）
.\.venv\Scripts\python.exe scripts\live_acceptance.py --mic             # 条件就绪后跑真实链路
```

- 操作手册：[`docs/LIVE_ACCEPTANCE_GUIDE.md`](LIVE_ACCEPTANCE_GUIDE.md)
- 证据：`docs/evidence/<YYYYMMDD>-live-acceptance/`
- 边界：Mock 通过不等于真实通过；体检未就绪时不会开跑 live。
- 门禁记账（2026-11-09 登记 `live_acceptance_guard` 后复跑）：磁盘 `test_*.py` 180 /
  已登记 178 / 清单 2（`scripts/test_gate_coverage.py` 输出）。

### 门禁报告（一条命令确认"每道门都真的跑过"）

```bash
.\.venv\Scripts\python.exe scripts\run_gates.py                 # 全跑，跳过即失败（道数以输出为准）
.\.venv\Scripts\python.exe scripts\run_gates.py --allow-skip    # 跳过只告警（兼容旧口径）
.\.venv\Scripts\python.exe scripts\run_gates.py --only protocol,core,evals
```

为什么单独立这个命令：若干回归脚本在环境不满足时会打印 `SKIP` 并以 0 退出
（`test-ui-nav-browser.mjs` 在 8000 未起时就是这样；`test-body-coordination-browser.mjs`
等缺本地夹具时同理）。逐条看退出码全是绿的，但那一轮其实什么都没验。
`run_gates.py` 把每条命令的退出码与输出一起收上来。裁决口径（R51 修掉的 fail-open）：**退出码非零一律判 FAIL**，哪怕输出里带 `SKIP` 行——所以 `--allow-skip` 也不会把「打印了 `SKIP` 却又退出非零」的真实失败洗成绿；只有退出码为 0 且命中 `SKIP` 才算「这轮没真跑」，单独列一行并以非零退出（`--allow-skip` 时降为告警）。**绿必须来自真跑过，不能来自没跑，也不能来自把失败伪装成跳过。**

浏览器门禁需要本地服务在 8000；服务未起时先
`.\.venv\Scripts\python.exe scripts\run.py --port 8000`。

改动 `web/` 前端文件后必须杀掉旧服务进程再重启，否则 `StaticFiles` 缓存的目录
列表会让新增前端文件持续 404，浏览器回归会假失败。

### 换 worktree / 换机器前：本机产物自检

`run_gates.py` 的门禁依赖一批**不在 git 里**的本机产物（`.venv`、`node_modules`、
`.agents`、`web/models/`、`pixel-world/` 等）。缺项报出来的错常常像代码坏了——
`durable_*` 抛 `ERR_MODULE_NOT_FOUND`、`venv_lock` 说解释器缺失——同一个人在同一个
仓库换一棵 worktree 就得到相反结论。换树前先跑：

```bash
.\.venv\Scripts\python.exe scripts\dev_env_check.py
```

它只读、不创建、不下载，逐项给出「在 / 缺 + 影响哪些门禁 + 怎么补」；门禁清单现读
`run_gates.GATES`，不复制会过期的名单。**它不是门禁**，不进 `run_gates` 的判定，
退出码只在「必须项」缺失时为 1。

| 本机产物 | 必需度 | 缺了影响 |
|---|---|---|
| `.venv` | 必须 | 全部 Python 门禁（缺了 `run_gates` 记 SKIP 并给 `uv sync --frozen`） |
| `node` / `npm` 可执行 | 必须 | Node 侧门禁（TS / npm / 浏览器） |
| `node_modules/`（仓库根） | 部分 | `npm` 与浏览器门禁（缺了记 SKIP，补 `npm ci`） |
| `agent-runtime/node_modules/` | 部分 | `durable_*` TS 门禁（缺了记 SKIP，补 `cd agent-runtime && npm ci`） |
| `web/vendor/three`、`web/vendor/three-vrm` | 部分 | 真实页面 importmap；现有浏览器门禁 stub 了 avatar 运行时，实测不依赖（人工看页面需要） |
| Playwright Chromium | 部分 | 浏览器门禁（缺了记 SKIP，补 `npx playwright install chromium`） |
| `.agents` bundle | 部分 | `poteto_loading`（缺失时显式 SKIP，不判红） |
| `web/models/`（seed-daily 链） | 部分 | `seed_eye_*`（缺资产时 SKIP；第三方许可资产不入库） |
| `pixel-world/dist/` | 部分 | `step8_e2e_browser`（缺了记 SKIP 并打印构建命令） |
| `127.0.0.1:8000` 本地服务 | 部分（运行时） | 浏览器门禁（未起时多数 SKIP） |

「必须」= 缺了门禁主体成片 SKIP；「部分」= 只影响表里列出的门禁，其余照跑。
2026-11-09 起 `run_gates.py` 自己登记了这些依赖物（`ARTIFACTS`），缺项记 SKIP
并打印补救命令，不再报成 FAIL。
补齐命令见脚本输出；不要替别人的机器自动创建或下载。本机多 worktree 可用
junction（`.venv`、`node_modules`、`agent-runtime/node_modules`），CI / 新 clone
一律按 `uv sync --frozen` 与 `npm ci`。

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

**2026-11-08 P0③ 收编进度**：原清单 8 条中 6 条已登记进 `GATES`（不进
`ALWAYS_REQUIRED`，不抬高 always 档）。当日余下 2 条都不登记，理由同一类——「跳过即失败」，
登记它们等于自开一个永不判红的洞：

- `test_eye_calibration_analysis.py`：本机与 CI 都没装 Pillow，脚本输出 `OK (skipped=14)`，
  14 条一条也跑不了。**补它需要**先建 `requirements-ci.txt` 并在 `.github/workflows/ci.yaml`
  装 Pillow，再确认 14 条能真跑，另开任务处理。
- `test_pixel_world.py`：世界源码已随 `7c13fd4` 剥离；仅本机有历史副本时可跑，新 clone 必
  SKIP。脚本仍留在磁盘，需要时手动跑。`pixel_world_hosting` 保留在 `GATES`——它用
  TestClient 打生产路由，守「`/` 必须是语音陪伴页」，不依赖世界源码。

**2026-11-09 seed 视觉夹具两条移出**：`test_seed_eye_expression.py`、`test_seed_eye_lids.py`
从 `GATES` 移出、登记进清单。它们读 `web/models/` 下的 VRM 夹具（`seed-daily.vrm` →
`seed-daily-eyes.vrm` → `seed-daily-lids.vrm`），整条链源自 VirtualCast, Inc. 的
`seed-san.vrm`（VRM Public License 1.0，`creditNotation: required`，派生须署名）；
而 `web/models/` 被 `.gitignore` 第 51 行整目录忽略、`git ls-files` 为空，新 clone 必
`ASSET_MISSING`。`ci.yaml` 第 10-11 行已声明这类「需要 gitignore 的 VRM 资产」的测试
不在 CI 范围，CI 那步又是「跳过计通过」——只有本机全量在逼它们必跑，而这个要求从未被
认领。把本地门禁对齐到已声明的 CI 范围，比把 11 MB 第三方许可二进制入库干净。

手动跑绿的条件：`web/models/` 里有上述三份 VRM 副本。此时
`.\.venv\Scripts\python.exe scripts\test_seed_eye_expression.py` 与
`scripts\test_seed_eye_lids.py` 分别输出 `9 tests` / `10 tests` OK（2026-11-08 主工作树
实测）。它们验的是「资产存在 + 构建可确定性重建 + 篡改必拒」，不依赖服务或 API Key。

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

2026-11-06 又补三条流程门禁，同样进 `ALWAYS_REQUIRED`，都是纯逻辑、零服务零 Key：
`process_trail`（决策 trail 每条必须有 `evidence` + `verification`，缺一即红）、
`process_trail_probe`（用 `check_probe.probe_self_check` 对 trail 副本做真实破坏注入，
证明校验器能红再信它说绿）、`risk_view`（爆炸半径分档 + `--risk` 只读、绝不跑门禁）。
复跑：`.\.venv\Scripts\python.exe scripts\run_gates.py --only process_trail,process_trail_probe,risk_view`。
注意 `check_probe` 本体早在 2026-10-27 就登记过了，`process_trail_probe` 是四判据的
**应用**门，不是重复登记；口径见 `docs/HANDOFF_20261105.md` §十。

2026-11-06 第五轮再补两条，同样进 `ALWAYS_REQUIRED`：`pacing_dead_keys`
（`pacing.yaml` 既有 15 个零消费方键登记为基线保留不删，新增未登记死键即红；
基线见 `scripts/pacing_dead_keys.txt`）、`fabrication_signal`（事实准确性护栏的
纯函数判据 + AST 断言信号不上 `done` 帧 / `TurnResult` / `app.py`）。
`scripts/test_fabrication_signal.py` 必须在 import 任何 server singleton **之前**
设 `DB_PATH=:memory:`（issue #39，`test_core.py:15-18` 同款），否则从 `run_gates`
子进程起来会被 `server/config.py:41` 判红。口径见 `docs/HANDOFF_20261105.md` §十一。

2026-11-06 第七轮补 `memory_recall_paraphrase`（同样 ALWAYS_REQUIRED）：守的是
`evals/memory_recall_{seed,paraphrase}.tsv` 这两份**纯合成**标注集与
`scripts/measure_memory_recall_paraphrase.py` 测量器的有效性（判别力 / 确定性 /
可归因 / 零隐私 / 不抬 `RECALL_FLOOR`），**不判召回率数字**。测量器非 `test_` 前缀、
只报数不判红，定位对齐 `scripts/measure_intent_corpus.py:2-4`。两份 TSV 由 Windows
写入带 BOM，读取必须用 `utf-8-sig`；种子与 `test_memory_retrieval_quality.py:CORPUS`
逐条一致由门禁断言守住。口径见 `docs/HANDOFF_20261105.md` §十三。

2026-11-07 第八轮补 `doc_drift`（同样 ALWAYS_REQUIRED）：旧入口 / 旧 CI 串行 /
旧用例数不得复活，新口径必须在，并从本文解析 `` `函数名` `:行号` `` 用 AST
比对 `server/app.py` 真实定义行、把用例数与 `evals/cases/*.yaml` 磁盘实况动态
比对。同名 `web/delivery-notice.test.mjs` 已进 `npm test` 链。口径见
`docs/HANDOFF_20261105.md` §十四。

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
- **离线可量的产品维度（2026-11-08 P0③ 补两格；不新增门禁）**：`extraction-quality-offline` 用 6 条应留 / 8 条应丢的合成标注候选喂 `parse_extraction`，打印并硬断言 precision=1.0、recall≥0.90（实测 14 候选 / 实留 6 / 丢弃 8，precision 1.000、recall 1.000）；它量的是**模型输出之后**的后处理判据，**不是模型抽取能力**——真模型的抽取精确率/召回率依赖 `LLM_API_KEY` 的 live 语料，未验证。`tts-emotion-routing-offline` 断言七档情绪各有专属 `voice/instruction/rate/pitch/volume` 且 `voice_profile()` 不回落 base（实测 7 档、instruction 去重 7、rate/pitch/volume 组合去重 7）；它量的是**路由**，**不是听感**——「这个情绪听起来像不像」依赖 TTS provider 凭据 + 固定脚本文案 + 人耳/评测集，未验证。两条都 offline、零 Key；合成小样本不作生产基准。
- **xfail**：`status: xfail` 的用例照常运行并在汇报中列出，但不计入失败（必须写 `reason`）。
- **offline 用例清单（条目数以 `evals/cases/` 目录实况为准，别信这里的数字）**：`fastloop-nonblocking-offline`（快环不阻塞，事件序断言）、`fewshot-emotion-tag-offline`（few-shot 示例带合法情绪标签）、`hallu-empty-retrieval-offline`（空检索必须注入负向约束）、`hard-delete-offline`（SQLite 物理硬删除含 `integrity_check`）、`lesson-injection-offline`（程序性 lesson 检索）、`tier-assembly-offline`（三档置信度装配顺序）、`barge-in-timeline-offline`（T4，2026-10-25 新增：`barge_in` 打断链必须发 `interrupted`→`playback_stopped`，且打断后旧轮迟到的 `audio`/`text_delta`/`done` 一帧不得逃逸；事件序断言不用墙钟，复用 `test_speech_performance_route.py` 的 in-process AST 提取 harness，纯离线零 Key、不起 uvicorn、不占 8000/8010、只读既有 WS 事件）、`valid-time-history-delivery-offline`（时效性历史投递）、`valid-time-future-delivery-offline`（时效性未来投递：未来界计划带 time_note、措辞不得对整条记录断言非现状）、`persona-negative-emotion-boundary-offline`（persona 必须声明「用户要求停止调侃时立刻停止」的边界；**这是配置层守卫、不是行为验证**——它只证明约束写在 persona 里，真实日志中「用户说别调侃了、Agent 仍继续调侃」的违反样本需 live 跑才能抓，配置层管不住。用例红即表示有人把这条状态从 persona 删掉或改坏了）、`persona-voice-style-offline`（persona 必须自述简短口语语音风格与去文字框化，且状态机取值仍合法——两条都是配置层守卫，不证明模型照做）、`empathy-support-offline`（用户发泄 / 低落时「不追问、不评判、先接住」的 persona 规则必须装配进 prompt；配置层守卫，非行为验证）、`proactive-boundary-offline`（主动发言的授权门 / 冷静期 / 安静模式约束在配置装配中可见；配置层守卫，非行为验证）、`offline-emotion-tags-offline`（2026-11-06：mock 回复池、`examples`、`stage_samples` 每条都带合法 `[情绪]` 标签，且固定台词必须真的解析出标签并从播出文本剥掉；带条数下限棘轮防真空绿，纯离线零 Key）、`proactive-authorization-runtime-offline`（2026-11-07：补 `proactive-boundary-offline` 自承的「非行为验证」缺口——用 AST 抽 `orchestrator.py::_proactivity_allowed` 真实函数体离线执行，枚举 5 组 `(cfg, state)` 断言 `require_authorization` 在运行期确实挡住未授权主动发言，尤其「`default_enabled` 翻 true 也不能绕过授权」；零 Key、不 import server 包）、`extraction-quality-offline`（2026-11-08 P0③：抽取后处理的 precision/recall，合成标注集；非模型抽取能力）、`tts-emotion-routing-offline`（2026-11-08 P0③：七档情绪到 TTS 参数的路由闭合；非听感验证）。

  数条数用 `grep -l "^layer: offline" evals/cases/*.yaml | wc -l`，别手写——这条清单的标题数字与条目数都会随新增用例漂移（HEAD 上是「2026-10-25 实况 7 条」且只列 7 个用例，而 2026-10-26 目录实况已有 11 条 offline；2026-11-05 又新增 2 条，一律以上面判据命令为准），原因是每加一条用例只补了条目、忘了改标题。

- **judge 三态（T5，2026-10-25）**：live 层可挂异源 LLM 裁判（`evals.checks:judge_hallucination`，`check.type: custom`，函数返回三元组）。判定分三态 `pass` / `fail` / `review`：正则与裁判**一致才定论**；裁判未配置、调用失败、输出无法解析、自报 unclear、或与正则不一致，一律 `review`——既不计通过也不计失败，交人工复核，只有 `fail` 计入退出码。裁判槽位是 `llm_judge_provider / llm_judge_base_url / llm_judge_api_key / llm_judge_model`，**设计上必须与被测模型不同源**（同源自评有自我偏好偏差）；留空即不启用，该 check 整层退化成「全 review」，不会把无 Key 的跑态假装成判过。**当前偏离（用户 2026-10-25 决定）**：judge 用同一供应商的另一模型（`step-5-preview` 判 `step-3.7-flash`），槽位独立但并非不同源——后果是 judge 永不单独把用例判红（`evals/checks.py` 的 judge 段注释写明了这条及其原因），换模型或改判定逻辑时须连同那条注释一起复核。**2026-11-05 口径补充**：上面「judge 永不单独判红」是 2026-10-25 用户明确决定的三态取舍（同源裁判假阳性会误红 paid live run），不是遗漏；本次新增「异源裁判严格模式」开关，只有 `LLM_JUDGE_PROVIDER` 与 `LLM_PROVIDER` 不同源时才允许 judge 单独判 fail 计入退出码，默认同源行为不变。judge 永不进 CI（CI 无 Key）。去偏手段只用论文本身支持的（交换顺序两跑取一致、judge 先给思维链），归属见 `docs/EVAL_MECHANISM_RESEARCH.md` §2.5。离线契约测试见 `scripts/test_judge_three_state.py`，覆盖五类：三态判定路径、`run_case` 端到端（含裁判客户端构造路径回归）、真实 HTTP 往返、退出码分桶、`main_async --live` 端到端；后四类全部零凭据零外网，`RESULTS_DIR` 指向临时目录，不写 `evals/results/`。**条数以实跑输出为准**（`.\\.venv\\Scripts\\python.exe -B scripts\\test_judge_three_state.py`），本文不写死——它随用例增减变化，写死就会变成三个互相矛盾的旧数字。

### 写动作崩溃矩阵（2026-10-20）

`scripts/probe_outbox_crash.py` 用两个子进程分别在“副作用后、回执前”和“回执已落盘、事项投影前”硬退出，再由父进程走真实 `AssistantService` 启动路径验收：前者转 `unknown` 并阻断 resume，后者只补投一次且不重复副作用。它不碰生产数据，已纳入 `run_gates`。

### 程序性记忆评测夹具（2026-10-20；2026-10-25、2026-10-26、2026-11-08 修订）

`scripts/` 下这批程序性记忆评测依赖的 `eval/research/*.json` 曾随 `/eval` 清理被删、未入 git。2026-10-25 已把「整组 `SkipTest`」改成「逐条 / 逐类 `skipUnless`」；2026-11-08 起，`source_projection` / `boundaries` / `diagnostics` 的夹具改由 `scripts/pm_eval_fixture.py` 与 `scripts/pm_boundary_fixture.py` 随测试离线重建，不再需要外部文件。**这 5 条（含 `snapshot`）都已登记在 `run_gates.py`**，不是 `scripts/gates_unregistered.txt` 里那两条（`test_eye_calibration_analysis.py` / `test_pixel_world.py`）。

| 文件 | 真跑 | 按理由跳过 | 缺什么 |
|---|---|---|---|
| `test_procedural_memory_eval_snapshot.py` | 22 | 0 | 无——2026-11-08 用目录 junction 构造等价 reparse point，不再依赖 symlink 权限 |
| `test_procedural_memory_source_projection.py` | 10 | 0 | 无——共享 3 bundle × 2 recipe 合成夹具（`pm_eval_fixture.py`） |
| `test_procedural_memory_boundaries.py` | 11 | 0 | 无——四条固定 id/role 由 `pm_boundary_fixture.py` 构造，`load_sources()` 与 `_check_recipe()` 双重校验 |
| `test_procedural_memory_diagnostics.py` | 15 | 0 | 无——共享 cases + `pm_eval_fixture.build_diagnostic_inputs()`；哈希按 `fingerprint(messages)` 自洽，provenance 明写 synthetic replay |
| `test_procedural_memory_eval.py` | 4 | 20 | `procedural_memory_eval_cases.json` + 配套 oracles（**仍不合成**，见下） |
| `test_procedural_memory_induction.py` | 8 | 0 | 无——`pm_induction_fixture.py` 转发 `pm_eval_fixture.build_sources()`，统一 payload |

条数为 2026-11-08 本地实跑值，会随用例增减变化，**以运行输出为准**。补夹具后跳过会自动转真跑；引用这些层的历史结果时仍须标明"夹具缺失、未复跑"。

**夹具合成边界（2026-11-08 校准旧结论）**：

- **eval 的 20 条**：`load_specs()` 要一份固定规格（3 sources / 6 targets / groups 恰为 `N,E,P,G` / 预算常量精确匹配），而 `gold_material()` 读的是 `source_oracle['human_card']`——**金标准**。合成它等于让评测拿我编造的答案自证，比诚实跳过更糟。因此这 20 条继续诚实跳过，待真实夹具或单独授权。
- **boundaries 的 6 条与 diagnostics 的 7 条，2026-11-08 已改为合成**，因为它们不涉及「金标准」：
  - boundaries 的具名边界不是靠名字自证：`load_sources()` 钉死 id/role，`_check_recipe()` 在 `prepare()` 时逐项断言（截断/歧义/并发唯一/并发歧义/缺 confirm 能力）。合成夹具若没真触发，会 `source_recipe_materialization_failed` 直接红——**旧文档「不保证触发」的担心已被代码兜住**。
  - diagnostics 的输入只校验 `original_input_sha256 == fingerprint(messages)` 等自洽条件，不校验历史出处；因此可以离线合成，但夹具里用 `provenance: synthetic_replay_not_a_historical_report` 与 `origin_report_path: synthetic-replay-not-a-real-report.json` 明示，不冒充真实历史报告。
- **source_projection / induction**：形状由 `SourceCorpus.prepare()` 与投影压缩条件唯一约束，合成夹具即等价覆盖。
- 仍然成立的原则：eval 的 gold 不合成；`.gitignore:58` 忽略的 `eval/research/*.json` 不作为分发手段（本地恢复了 CI 仍空跑），夹具随测试代码分发。

**2026-10-25 变更**：`scripts/test_memory_eval.py` 已改为内联合成夹具（`build_cases()` 内存构造 12 条，仍经真实 `load_cases` 逐条校验），不再 `SkipTest`；`ci_test_shard.py --workers 4` 的并发分片会真跑它（本地实跑 19 条 OK）。它是其余程序性记忆测试内联化的样板——正解不是恢复被 `.gitignore:58` 忽略的 `eval/research/*.json`（本地恢复了 CI 仍空跑），而是让夹具随测试一起分发。
- **新增用例**：每次出幻觉事故就加一条 YAML。字段见 `evals/cases/` 现例；`checks` 支持 `contains / not_contains / regex_any / not_regex / custom`，custom 判定函数写在 `evals/checks.py`，用例里以 `evals.checks:<函数名>` 引用。预置状态写 `setup.memories`，用户轮次写 `turns`，live 默认跑 `runs: 3` 次，通过线由 `pass_rule: all | min_rate:0.8` 控制。`origin` 必填（来源实验 / 事故 / commit）；缺 `origin` 或空串会在 `main_async` 起手处 fail-fast 并非零退出（`scripts/run_evals.py`）。
- **候选漏斗**：`scripts/mine_eval_candidates.py` 以 `mode=ro` 只读 `data/memory.db`（不 import 任何 server 模块、不写活库），按四类确定性启发式（共指/省略追问、疑似幻觉面、事实挑战、负面情绪边界）提名评测候选，凭据与个人信息脱敏后落 `eval/research/case_candidates_*.json`（`.gitignore:58` 覆盖，**不进版本库**）；候选经人工筛选与改写后才手写进 `evals/cases/`，脚本本身不写受版本控制的目录。库不存在时打印说明并 exit 0（正常态）。

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
# （该文件不存在，勿当作可跑命令——见下方说明）
.\.venv\Scripts\python.exe scripts\test_stalled_running.py      # M5 缺口复现：重启收口机制通路 + 会话内僵单无人收（第二段钉现状，主体落地后照 docstring 口径翻转）
.\.venv\Scripts\python.exe scripts\test_codex_worker_shim.py   # Codex shim 五种事件 + 真实 artifact ref（假 Codex，不联网）
node web\executive-state.test.mjs                               # 心流防打扰纯逻辑（零 DOM）
node web\ui_nav.test.mjs                                         # 界面自动领航（ui_nav）纯逻辑 + 心流护栏（零 DOM，无服务依赖）
node scripts\test-ui-nav-browser.mjs                             # 领航浏览器回归：抽屉自动开合 / 聚焦 / 子动作 / 侧栏·星图落点（需先启动本地服务；服务未起时自动 SKIP，不算失败）
```

> **已关闭（2026-10-31 提交 `0eb3625`）**：`scripts/test_verify.py` 现已存在（545 行、8 个 test，含 `test_io_ban`），`server/verify` 包不再是「零行为测试覆盖」——`check.py:30` / `verifier.py:30` docstring 承诺的「禁止开 socket、连库、subprocess」IO 禁令由 `test_io_ban` 机械强制，连同三种 check 正反例、reader 注入、IO 静态断言与 evaluator 桥接一并有测试覆盖。此前「文件不存在 / 零覆盖 / IO 禁令无机械强制 / 待用户拍板」的记录（2026-10-26 核）已被该提交推翻，不再适用。完整规格仍见 `docs/TOOL_OPERATION_EXPLORATION_20261024.md`（该文档属并发会话在飞工作，本线不改）。
>
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
.\.venv\Scripts\python.exe scripts\test_durable_supervisor.py # sidecar 监督：起得来/探得活/收得干净（需本机 node，缺则 SKIP；钉 script 模式，零外部网络）
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
.\.venv\Scripts\python.exe scripts\test_memory_temporal.py       # 事实有效时间保守解析（valid-time，见 MEMORY_RESEARCH_NEXT.md）
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
node web\context-budget.test.mjs                                 # 上下文预算展示：formatContextBudget 各段分支与拼接顺序（零 DOM，22 例）
node web\context-usage.test.mjs                                  # 本轮参考清单护栏：validId / validCount / appendContextUsage 前置条件（零 DOM，18 例）
node web\memory-panel.test.mjs                                   # 记忆面板纯函数：停用标签、更新时间、原话引用、停用/启用形状（零 DOM，11 例）
node web\test_glimmer_bubble.mjs                                 # 微光气泡状态机：show/expand/update/dismiss/poll 与回调缺省不抛（零 DOM，10 例）
node web\events-v2.test.mjs                                      # 契约 v2 §25 前端消费层：模拟 WS 帧驱动信封归约与 UI 状态归约（不起服务，28 例）
# explore.js（设计探索沙盒）经 2026-10-26 核实**故意不加单测**：它是纯数据(effects/presets)
# + DOM 挂载，presets 引用的 effect id 若写错会**响亮失败**（apply() 里 presets[state.preset].title
# 直接 KeyError、无效 data-fx-* 属性肉眼可见），不属 task-card/external-model 那类静默失效，
# 故按"不为凑覆盖率造边际测试"的纪律留白。若将来给它加纯函数导出，再按 memory-panel 范式补测。
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

## 已核实无缺口：模块级状态在提前退出时漏初始化（2026-10-26）

`web/assistant.js` 曾连续三次暴露同一缺陷家族——模块级状态只在成功路径赋值，而提前 return 或 await 窗口期间为 undefined，消费方要么没守卫、要么守卫本身把失败变静默：

1. `bootAssistant` 拉取 preferences 失败走提前 return，`actionArea` 未赋值 → 带 confirmation 的结果被 `if (!actionArea) return` 静默丢弃，有副作用的动作永远无法获批；
2. 修完 1 后连带暴露 `refresh()` 在 `tasks/reminders/inbox` 还是 undefined 时抛 TypeError，被 `button()` 吞成 `errorArea` 文案——一次已成功的确认也显示成错误；
3. preferences **仍在途**（不只是失败）时 gate 未建，同样静默丢弃。

三处均已修复，并由 `web/assistant.test.mjs`（4 例，零 DOM）钉住：面板未启动不抛不渲染、在途窗口确认门已就绪、启动失败后确认门可达、点批准真调确认接口且不留错误文案。

**横向扫描结论（同日，覆盖 `web/*.js` 顶层 + `web/` 子目录全部生产类 + 本人改过的 `server/` 文件）：该模式不广泛存在。**

- `web/` 下只有 3 个文件有模块级状态容器：`assistant.js`（上述已修）、`companion.js`、`avatar_lab.js`。
- `companion.js:15` 的 8 个状态全部在**第一个 await 之前同步赋值**，两个提前 return（`if (mount) return` 幂等守卫、`if (!mount) return` 查询失败）都在赋值之前，且每个消费方都有守卫（`refresh()` 的 `if (!mount)`、`renderSummary()` 的 `if (!summary)`、`associate()` 的 `if (notice)`）——这正是 `assistant.js` 当初缺失的那层保护，可作对照范式。
- `interaction-preferences.js` 有 12 处 await 但 **0 处 return/throw**，模式不适用。
- `avatar_lab.js:12` 的 `performanceProbe`/`performanceResult` 同样安全：5 处消费方全部可选链（`performanceProbe?.stop(...)`、`performanceProbe?.active`），`performanceResult` 有显式守卫（`if (!performanceResult) return`），并发启动被 `if (performanceProbe?.active) return` 挡掉，`finally` 里 `if (performanceProbe === probe)` 避免误清别人的探针；该文件两处 `return` 一处是提前退出、一处是**返回值**（`return [key, ...]`），都不在状态赋值路径上。
- `web/app.js:1364-1368` 的 `assistant_result` 分发无丢弃分支：要么 resolve 待确认的 `pendingConfirmation`，要么直接调 `showAssistantResult`，端到端通路完好（`contextBudget?.note(...` 为可选链）。
- `web/` 子目录另有 3 个生产类（`grep -rnE "^\s*class [A-Za-z_]+" web/ --include=*.js`，排除 `web/vendor/`、`web/archive/`、`test_*` 后全部命中），同样均非缺口：
  - `web/avatar/drivers/idle.js:40 DelayLine` 构造器赋满 `buf`/`max`；`web/voice/capture-worklet.js:3 DuplexCaptureProcessor` 构造器赋满 `frameSize`/`microphone`/`reference`/`used`/`running`——无遗漏字段。
  - `web/avatar/performance-probe.js:56 AvatarPerformanceProbe` 是唯一"字段多、构造器只给 4 个"的类（`start()` 一次性赋 22 个：`active/result/name/metadata/startedAt/started/durationMs/drainMs/rafIntervalsMs/avatarFrames/gpuFrames/pending/previousRaf/currentFrame/submissionIndex/restorers/rig/renderer/environment/gpu/gl/ext/_completion`），**但每个消费点都在 `active` 守卫之内**：`complete()` 有 `if (!this._completion) throw` 的显式守卫，`stop()` 首行 `if (!this.active) return this._completion || Promise.resolve(this.result)` 直接短路，走不到 `this.started`/`this.drainMs`/`this.gpu`；`_finish()`/`_deletePending()`/`_discardDisjoint()`/`_disableGpu()` 只可能由已在采样的 `drain()` 链抵达。这正是 `assistant.js` 缺失的那层保护——不是字段少，而是消费方**缺守卫**。
- `server/` 侧用 AST 扫「实例属性在非 `__init__` 方法赋值且不在 `__init__` 赋默认值、却被别处读取」，本人文件仅 2 处候选，均为 `store.py` 中 `Memory.disabled` / `RankingRow.disabled` 在 `__post_init__` 内做 `bool()` 归一化——同方法内自赋值自读取、在任何外部消费方之前确定性完成，属良性字段默认值形态。

**复现判据**（要再扫一遍时用，别靠印象）：`web/` 侧 `grep -nE "^let [a-zA-Z_, ]+;" web/*.js` 找状态容器，再对每个 `boot*` 函数核对「赋值是否都在首个 await 之前」与「消费方是否有守卫」；`server/` 侧用 AST 比对 `__init__` 与非 `__init__` 方法的 `self.X` Store/Read 集合差集。注意 `git status` 分不清本人与会话并发方的改动，扫 `server/` 必须用文件白名单。

## 已核实无缺口：except 静默吞异常（2026-10-26）

同一轮用 AST 扫了本人 15 个 `server/` 文件的全部 **108 个 except 处理器**（`ast.ExceptHandler`，判据是 body 是否仅为 `pass` 或仅含字符串 Expr）。结论：**8 处 `except-pass` 与 6 处"体仅字符串"全部是正确写法，无一处需要改。**

先说两个**判据本身的坑**，否则数字会被误读：

- 那 6 处"体仅字符串"是**我的扫描器误报**。脚本用 `body = [s for s in node.body if not isinstance(s, ast.Expr)]` 过滤，而 `await conn.finish(...)` 在 AST 里正是 `Expr`，于是被整条滤掉、显得"体只有字符串"。真实代码全都有实质动作（见下）。
- 另一处误报来源：早前我用 `grep -cE "except Exception:\s*$"` 统计出 orchestrator 12 处 / app.py 22 处，那个模式匹配的是**行尾的 `except Exception:`**（即多行 except），不等于吞异常；而 `except[^\n]*` 这种写法在 POSIX ERE 里 `[^\n]` 是"排除反斜杠和字母 n"的字符类，`Exception` 里正好有 `n`，永远匹配不到。**这个结构只能用 AST 数。**

逐处核实（判据：失败是否**既不可见又有后果**）：

> **2026-11-07 口径**：下表行号是 2026-10-26 的取证快照，之后多轮重构已普遍漂移
> （例如 `_confirm_action` 从 `:579` 到 `:623`、`shutdown` 从 `:1852/:1863` 到 `:1916`）。
> **函数名与判定结论仍有效，行号请以 `grep -n 'async def <函数名>' server/app.py` 重锚**，
> 不要把表里的数字当当前坐标。几个当前锚：`_confirm_action` `:643`、
> `_run_turn` `:894`、`websocket_endpoint` `:1461`、
> `_start_durable_if_enabled` `:1880`、`shutdown` `:1989`。
> （2026-11-09 重锚：`_service_data_dir` 上提到 `server/config.py`，app.py 因此
> 前移 19 行；锚点按 `ast` 实测更新，表内历史快照仍保留。）
> （2026-11-12 重锚：`/api/health` 的 tts/asr 段加了 fish 分支的模型名，
> app.py 因此后移 6 行；锚点按 `ast` 实测更新，表内历史快照仍保留。）
> 快照原文保留，不逐个改写——改写反而会把历史取证现场抹掉。

| 位置 | 形态 | 判定 |
| --- | --- | --- |
| `server/app.py:579` `_confirm_action` | `except Exception` → `conn.finish(turn, {type:"error", code:"assistant_confirmation_failed", message:"未能取得确认结果…", terminal:True})` | 正确：用户收到明确失败回合 |
| `server/app.py:1018` `_run_turn` | `except Exception as exc` → `conn.finish(turn, {type:"error", message:str(exc), terminal:True})` | 正确：明确错误回合 |
| `server/app.py:1066` `_transcribe_turn` | 同上，且经 `_transcribe_error_message()` 脱敏 | 正确：见 :1025-1031 注释，防供应商响应体外泄进字幕 |
| `server/app.py:622` `_narrate_turn` | → `conn.finish(turn, {type:"error", message:"这条记忆暂时讲不出来。", terminal:True})` | 正确：明确错误回合 |
| `server/assistant/context.py:182` `_memories` | → `result.update(status="unavailable", stated=[], inferred=[], uncertain=[])` | 正确：降级状态**显式写进结果**，下游走负向约束而非脑补 |
| `server/memory/presentation.py:135` | `except (ValueError, OverflowError, OSError): pass` | 正确：`:131` 的 `type(as_of) in (int, float)` 与 `:133` 的 `math.isfinite()` 已双重收窄，且 `:137-139` 紧接着给 `time_note` 兜底文案 |
| `server/dialogue/orchestrator.py:535` | `except Exception: pass` | 正确：注释声明"fast loop: never let a trace break a turn"，是**追踪落盘**（非功能路径），带 `# noqa: BLE001` 表明显式决策 |
| `server/dialogue/orchestrator.py:681` `_turn_context` | `except KeyError: pass` | 正确：`thread` 保持未绑定，`:689` 的 `bool(thread)` 使 `companion_context_available` 如实为 False——失败**可见** |
| `server/app.py:1555` `websocket_endpoint` | `except WebSocketDisconnect: pass` | 正确：`finally` 里 `detach_v2()` / `_connections.discard` / `conn.close()` 照常收尾 |
| `server/app.py:1595` `proactive_socket` | `except (WebSocketDisconnect, json.JSONDecodeError): pass` | 正确：`finally` 里 `conn.close()` + `store.end_session()` 照常收尾 |
| `server/app.py:1852/:1863` `shutdown` | `except CancelledError: pass` | 正确：关停期被取消是预期，不该报错 |
| `server/app.py:1791` `_start_durable_if_enabled` | 内层 `except Exception: pass` | 正确：外层 `:1786-1790` 已 `print(f"[durable] 执行内核不可用：{exc}")` 明说，内层只是关停一个已经起不来的 supervisor；派单会转 BLOCKED 并说明不可用 |

**可复现的判据**（替代上面那两个会骗人的正则）：用 AST 遍历 `ast.ExceptHandler`，检查 `node.body` 是否长度为 1 且为 `ast.Pass`；若要进一步判断"是否等效空操作"，必须把 `ast.Expr` **按语句类型细分**——`await X` / 赋值 / 函数调用都是 `Expr` 但有实质动作，只有 `ast.Constant`（字符串）才是真空操作。早先那版扫描器把整个 `Expr` 类滤掉，才产出 6 条误报。

**真正值得改的形态长什么样**（本仓现存范本）：`server/dialogue/orchestrator.py` 的 `notice_error`——`except Exception` 不再 `pass`，而是 `self._last_meta["notice_error"] = str(exc)[:200]`，与既有 `extract_error` 同形，失败进入可观测处而不是消失。见到"吞掉"先问一句：**这个失败有没有一个用户或下游能看见的落点**；有，就别动。

**复现判据**（要再扫一遍时用，别靠印象）：`web/` 侧 `grep -nE "^let [a-zA-Z_, ]+;" web/*.js` 找状态容器，再对每个 `boot*` 函数核对「赋值是否都在首个 await 之前」与「消费方是否有守卫」；`server/` 侧用 AST 比对 `__init__` 与非 `__init__` 方法的 `self.X` Store/Read 集合差集。注意 `git status` 分不清本人与会话并发方的改动，扫 `server/` 必须用文件白名单。

## 已核实无缺口：eval 用例配置一致性（2026-10-26）

对 `evals/cases/` 全部用例（2026-10-26 取证快照为 16 个：5 live + 11 offline；**2026-11-07 复核当前 23 个：7 live + 16 offline**；**2026-11-08 复核当前 25 个：7 live + 18 offline**；当前条数以判据命令 `grep -l "^layer: offline" evals/cases/*.yaml | wc -l` 为准，勿信本文写死的任何数字）做过一轮配置一致性核查，结论：**框架本身已堵住主要静默失效入口，本轮只修了两处文档/配置漂移。**

`scripts/run_evals.py` 的加载器已硬校验六类危险配置，任一出现即 `return 1` 让门禁变红，不会静默放过：
`runs` 不是正整数（0 或负值让 `passed_runs == runs_n` 变成 `0 == 0` 判 PASS）、`checks` 为空或形状损坏（空列表让 `all([])` 恒真、一条断言都不跑却判 PASS；非 list / 条目缺 type 会在 run_case 崩）、offline 用例用 `target: reply`（offline 路径 replies 恒为 `[]`，负向 check 永不命中）、`pass_rule: min_rate:` 阈值不在 (0,1]（阈值 0 会让 `passed_runs/max(1,n) >= 0` 恒真，断言全红仍判过）、`turns` 为空（一次对话都不发生）、seed 记忆良构不达标（`setup.memories` 条目非 dict、`key`/`value` 空或非字符串、`fact_valid_from/until` 非数值——空 value 令排序检查因 `"" in block` 恒真、`block.find("")==0` 而空转 PASS，缺 key/value 与非数值 fact_valid 令 seed_memories 崩整轮）。这六类的修法历史记在加载器注释里，排查时先读那里。

**本轮修的两处**（都不改变行为，只是把"靠默认值兜底"变成显式）：
- `evals/cases/lesson-injection-offline.yaml` 补 `layer: offline` 与 `status: active`，并删掉行首那行**无人读取的 case 级 `target: prompt`——`target` 是 check 级字段，代码只读 `check.get("target", "reply")`。该文件是当时 16 个用例中唯一缺 `layer`/`status` 的；此前靠默认值（无 layer → 落入 offline_cases；无 status → 非 xfail）恰好行为正确，但默认语义一旦变更就会静默换层。
- `docs/TESTING.md` 的 offline 用例清单标题原写"实况 8 条"，实际列 9 条、目录实为 11 条（数字与清单均过期）。已改为不写死数字 + 给出 `grep -l "^layer: offline" evals/cases/*.yaml | wc -l` 的判据。

**排查这类问题时的判据（我本轮被自己的正则骗过三次，别再犯）**：
1. 找函数定义必须兼容 `async def`——只写 `^def ` 会把所有 judge 类检查函数漏掉，从而报出 5 处"引用了不存在的函数"的假警报。
2. 匹配 YAML 的 `target:` 必须兼容 `  - target:` 这种带短横的列表项形式——只写 `^\s+target:` 会漏掉绝大多数，把 30 处 `prompt` 误报成 1 处。
3. 只扫"带 custom 引用的文件"会漏掉无引用的用例——我曾因此以为 live 只有 3 条，与日志"跳过 5 条"对不上，实际是漏扫了 `accuracy-over-persona-live` 与 `hallu-empty-retrieval-offline`。

**带 bug 的校验器比没有校验器更危险**：它会用"全部 OK"或"5 处违例"给出确定语气，而两者都是错的。每条结论落地前先用一个已知答案的样本反验校验器本身。

## 已关闭：已跟踪的「可执行配置」引用了未跟踪文件（fresh clone 会崩）

**2026-11-05 回填（已关闭）**：实测这批文件现已全部 tracked——2026-10-29 提交 `0f87efa` 入库四处引用，2026-10-31 提交 `0eb3625` 补齐 `scripts/test_verify.py`、`5b03d38` 补齐 `scripts/echo_server.py`。fresh clone 不会再因这批引用 ENOENT。**下方表格与叙述是 2026-10-26 的取证快照，勿当当前状态**；它记录的是当时的破坏面与复现判据，复现判据命令仍有效（可用来复核「已跟踪文件是否仍引用了未跟踪文件」这条不变式），但表里「未跟踪 / fresh clone 会崩」的结论已被上述提交关闭。表格原文保留不改。

**这不是当前工作树的问题**——未跟踪的文件都在磁盘上，所以 `npm test` / CI / 覆盖度棘轮此刻全绿。问题是 **clone HEAD 之后**：已跟踪的配置会去调用一批不存在于 HEAD 的文件，于是 ENOENT。

四处，均已实证（行号会漂移，以文件内容为准）：

| 已跟踪的引用方 | 指向的未跟踪文件 | fresh clone 后的破坏 |
| --- | --- | --- |
| `.github/workflows/ci.yaml` 的 memory-contract 步骤 | `scripts/check_memory_contract.py` | 该 CI 步骤 ENOENT、非零退出，CI 红 |
| `package.json` 的 `test` 脚本 | `web/assistant.test.mjs`、`web/companion.test.mjs`、`web/external-model.test.mjs`、`web/task-card.test.mjs` | `npm test` 在第一个缺失文件处 ENOENT，`&&` 链中断，后续套件全不跑 |
| `scripts/gates_unregistered.txt` 的清单 | `scripts/test_memory_snapshot.py`、`scripts/test_memory_temporal.py` | 覆盖度棘轮不变式是「登记 + 清单 = 磁盘」，磁盘少 2 条即不成立，`test_gate_coverage.py` 红 |
| `server/memory/manager.py` 的 import | `server/memory/temporal.py` | 若单独提交 manager.py，整条 `server.memory` 导入链 `ModuleNotFoundError`（此条 `docs/MEMORY_VALID_TIME.md` §10 已记） |

**为什么会这样**：这些文件大多由本线迭代新建（`check_memory_contract.py`、四个 `.mjs` 测试、`temporal.py`/`snapshot.py` 及其测试、三个 valid-time eval 用例、`MEMORY_VALID_TIME.md`），而本线**不提交**，于是"新文件 + 改配置"同时留在工作树里，配置先于文件入库。

**决策点（需用户拍板，勿自行 git add）**：这批未跟踪文件是否随本轮一起入库。若入，需保证原子性——尤其是 `manager.py` ↔ `temporal.py`、`package.json` ↔ 四个 `.mjs`、`ci.yaml` ↔ `check_memory_contract.py`、`gates_unregistered.txt` ↔ 两个 `test_memory_*.py`，四组都必须同一批提交，否则等于把地雷从工作树搬进 HEAD。

**复现判据**（要再扫一遍时用，别信上面这份静态清单——它会随提交状态变化）：
- 引用方向：`git ls-files --others --exclude-standard` 取未跟踪集合，再在**已跟踪**文件里搜这些路径。注意过滤器别写 `startswith(".git")`——那会连 `.github/` 一起漏掉（本线第一遍扫描就因这个漏报了 ci.yaml）。
- import 方向：对每个未跟踪 `.py` 取模块名，在已跟踪 `.py` 里 AST 扫 `Import`/`ImportFrom`（`level == 0`）。
- 判据是**可执行引用**（CI 步骤、`package.json` 脚本、门禁清单、import），文档里提到文件名不算——那只会让文档描述不存在的东西，不会让命令失败。
