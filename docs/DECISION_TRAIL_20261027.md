# 决策记录：2026-10-27 快环阻塞调用卸载 sweep（8 小时自主迭代）

作者：Chief（poteto-mode A 档）｜日期：2026-10-27｜会话形态：ultracode 8 小时不间断迭代，方向与决策自主。

本档是 51 个提交的决策 trail 提炼版，完整版（含每次探针的原始数据、每次 review 的逐条结论、每次共享文件手术的细节）在本机 `tmp/ITERATION_LOG.md`（gitignored）。本档只留**需要跨会话存活的部分**：判定、触发器、待拍板项。

## 一、批次总表

| 批 | commit | 动作 |
|---|---|---|
| 1 | `05768ac` | #59：lessons 扫描边界从 `limit=1000` 改用 COUNT 真实行数，修静默截断 |
| 2 | `1102e44` | 心境衰减 + mark_used 卸载出事件循环 |
| 3 | `c9447a1` | 台账读卸载，open_commissions 补显式上界 |
| 4 | `221f5e3` | stage_sample_line 按 (path, mtime_ns) 缓存，修每调用读盘 |
| 5 | `cb5646d` | ASR 模型加载移出事件循环，anyio.to_thread 切 asyncio.to_thread |
| 6 | `97d73e6` | 日记合成输入按最近上限截断，messages_between 补 limit |
| 7 | `36118a6` | scan_due 写事务卸载出事件循环 |
| 8 | `acc720f` | venv_lock 门禁：跑门禁的解释器必须与 uv.lock 一致 |
| 9 | `10f6b8e` | tmp_sweep 清扫机制与计数棘轮（AGENTS.md 待办第 3 条） |
| 10 | `9a25f54` | WS 轮次的消息落库与历史读取卸载 |
| 11 | `d9528f2` | 主对话路径的历史读取与消息落库卸载（MEM-OPT-01 step2） |
| 12 | `cc672e6` | lessons_for 并入线程外批次（#59 根因） |
| 13 | `bdbf8c1` | S4 占比数据 24 色重测（R2），关闭 #15 |
| 14 | `fe266ef` | memory_concurrency 门禁：检索并发不变量（此前零覆盖） |
| 15 | `39b7ae3` | memory REST 四条路由卸载 |
| 16 | `565e21f` | 四个语音处理器卸载 |
| 17 | `297f025` | 两个 WS 端点 7 处会话调用卸载 |
| 18 | `26b244f` | v2 五命令 8 处台账调用卸载 |
| 19 | `c06fdd7` | app.py 自有 memory REST 路由卸载 |
| 20 | `dd7fcab` | 剩余后台处理器写事务卸载，判据换为「写卸读留」 |
| 21 | `285e765` | 三条 REST 路由补 limit 夹取，堵 SQLite LIMIT -1 无界读 |
| 22 | `819d603` | transcript 路由记录「刻意不加截断信号」判定 |
| 23 | `bb3b7c7` | usage sweep 批量写卸载 |
| 24 | `bf005d4` | observe_turn 两个写卸载 |
| 25 | `886558a` | self-evolution 循环 14 处写卸载 |
| 26 | `0996b92` | 两个 REST 路由器 6 处 service 写卸载 |
| 27 | `91b56e6` | 首字延迟受控 A/B 测量归档进 LATENCY_BASELINE.md |
| 28 | `0fe3357` | TESTING.md 去掉无锚现状数字 |
| 29 | `379926f` | 本决策记录首版入库 |
| 30 | — | 引擎侧检索连接方盘点：#31 触发器②未触发 |
| 31 | — | 增量索引方案实测不可行（`fit`+`transform` ≠ `fit_transform`），#31 判定链补证据 |
| 32 | — | #9 toAltar 判定：两项修复已在 HEAD，真跑需隔离实例 + 真实 Key |
| 33 | `bdbf8c1` | S4 占比 24 色重测 R2，关闭 #15 |
| 34 | `1d53607` | broadcast_e2e 门禁：跨线程广播链端到端（判别力：破坏版 0 帧） |
| 35 | `21320cc` | shared_retriever_concurrency 门禁：共享 retriever 实例并发检索 |
| 36 | — | 并发 session 的 M1-M4 WIP 只读审查：四套测试真跑真过 |
| 37 | — | 索引冷重建成本门禁：三个判据全部被实测证伪，判定不交付 |
| 38 | `faa4a88` | memory_retrieval 门禁登记进 run_gates（认领机制首例） |
| 39-42 | `52dd162` `ee8c28b` `9be5bb8` `c811e56` | 门禁认领四批：71 个未登记测试进门禁，本地门禁 51→146 |
| 43 | `c1663a5` | TESTING.md 补「门禁外的测试」一节，说明清单构成与认领方式 |
| 44 | `b03b792` | 逐条判定剩余 16 个门禁外测试，再认领 12 个，清单降到 8 条 |
| 45 | `fee1516` | 四个浏览器门禁删掉自己的沙盘目录，止住 tmp/ 只增不减 |
| 46 | `c1dd5a7` | `--always` 参数：一条命令跑「必须永不跳过」那一档 |
| 47 | — | 沙盘修复后全量终验：190 跑过并断言 / 6 失败，逐个归因 |
| 48 | `47aadb5` | fastloop [2]/[5] 判据：绝对毫秒换成同步对照的心跳数比值 |
| 49 | `89c8036` | fastloop [6] 段同改；原增量判据实测恒为负 |
| 50 | — | ALWAYS_REQUIRED 判据安全边际巡检：49 个门禁无可疑项 |
| 51 | — | #31 触发器②复验（HEAD `89c8036`）：仍未触发 |

并发 session 同期提交 `aa9c633`（评测机制加固，含 evals_health 层与检索质量门禁），本会话未改动其文件。

| 批 | commit | 动作 |
|---|---|---|
| 52 | — | TESTING.md 门禁口径核对：结论是数字写法全部合格（「道数以输出为准」），但查出「哪些测试在门禁外、为什么」只写在清单头部、正文无位置，补一节 |
| 53 | — | `echo_server.py` 缺失影响面盘点：受影响门禁只有 `durable_mcp` 一个，属并发 session 的 M1-M4 WIP 范围 |
| 54 | `fee1516` | 四个浏览器门禁删掉自己的沙盘目录，止住 tmp/ 只增不减（三次迭代才修对，见方法论 9） |
| 55 | `c1dd5a7` | `--always` 参数：一条命令跑「必须永不跳过」那一档 |
| 56 | — | 沙盘修复后全量终验：190 跑过并断言 / 6 失败，逐个归因 |
| 57 | `47aadb5` | fastloop [2]/[5] 判据：绝对毫秒换成同步对照的心跳数比值 |
| 58 | `89c8036` | fastloop [6] 段同改；原增量判据实测恒为负 |
| 59 | — | ALWAYS_REQUIRED 判据安全边际巡检：49 个门禁无可疑项 |
| 60 | `9608585` | 决策记录补第 29-51 批 |
| 61 | `4153bd6` | `check_staged_scope`：把「别卷入他人文件」变成退出码 |
| 62 | `0485013` | 派单宪章补「提交范围自检」节 |
| 63 | — | R51 裁决口径与门禁认领的相容性核验：天然一致，无欠账 |
| 64 | `c633eb1` | R51 裁决口径门禁：合成门禁钉住完整裁决表 |

另有 `1b993cf`（会话前既有提交）与 `abe6bd9`/`3afb386`（文档入库与门禁裁定）。

每批均过四道黄金门禁（`check_protocol.py` EXIT=0、`test_core.py` 92 passed、`run_evals.py` 11 passed 0 failed、`npm test` EXIT=0）+ 本批新增/扩展门禁 + subagent review。passed 数会随用例增长，以实跑输出为准，此处是 2026-10-27 21:35（HEAD `89c8036`）的记录值。

## 二、卸载判据（第十六批定型，后续各批沿用）

**写事务一律卸，有界索引读留同步。**

- 写 = `BEGIN IMMEDIATE` + commit，默认 `synchronous=FULL` 每次 fsync。实测家族成本 4-18ms/次（第十六批：单行 UPDATE median 3.97ms、upsert median 9.82ms、accept worst 9.42ms；第二十三批：usage sweep 200 行批量 worst 37.86ms）。
- 读 = 不脏页、commit 近乎零成本，实测 0.07-0.5ms。`to_thread` 调度开销本身 0.248ms，卸亚毫秒读是净亏。
- 无界增长读（O(表长)）按写对待：`store.count()` 1000 行 0.34ms / 5000 行 2.16ms，history_of 全表扫描，均卸。

**不为不可达场景加机器**（Laziness Protocol）：并发来自默认执行器 worker，但快环真实并发 1-2 连接；8 线程混合读写不是生产形态，故不拆存储层单 RLock（第二十五批实测并行比 0.13 也不拆）。

## 三、三个 revisit 触发器（均有现状依据）

| 触发器 | 现状依据 | 出处 |
|---|---|---|
| 索引 convoy 重估：库过 3000 条或第二检索连接方 | 个人库 3 条；引擎仓库对 durable 检索端点零引用 | 第八批 + 第三十批 |
| 拆 store 单 RLock：并发 ≥4 成常态 | 单用户 1-2 连接；SQLite 单连接本身就是串行瓶颈 | 第二十五批 |
| resume TOCTOU 加锁：多标签页同 session 并发重连 | 需两标签页同旧 id 在 ~5ms 内同时重连；后果良性 | 第三十一批 |

第三十一批同时诚实记录了一个**我引入的回归窗口**：第十二批把 resume 分支卸载后，「未被占用检查」与 `conn.session_id = candidate` 赋值之间出现 await（卸载前是原子块）。不修的理由同上表，触发器写死。

## 四、三个方法论沉淀

1. **受控 A/B 分离环境漂移**。跨天比延迟数字等于把环境和代码混在一个量里。第二十六批用 `git worktree add` 到会话前父提交做同时段同机器对照，证明卸载批次对首字延迟零影响（会话前 109.8ms vs 卸载后 108.4-108.8ms），与 2026-10-24 基线的 +13ms 全归环境漂移。worktree 用完即 `git worktree remove --force`。
2. **探针必须打点验证测的是真路径**。本会话三次踩坑：第十四批探针文本被正则提前截断，量的不是目标分支；第二十八批 settle_pending 探针用同一条消息当 current，行不够结算条件，量到的是读路径；第二十三批 epiphany 边为空使一处卸载不可达。「不修」的结论比「修」的结论更需要这个证明——一个量错分支的探针能把误判伪证成数据。
3. **「保留同步」的代价不只是它自己的读耗时**。store 单连接 + 单 RLock，轻读会排在已卸载的写后面等锁（第二十五批实测混合负载读中位数被写抬到 18-33ms）。判轻路由要在「读耗时 + 等锁时长」上算，不是单机空载数字。
4. **判据本身要先被验证，别拿字面印象当判据**。本会话有五次「判据被实测推翻」：冷重建成本门禁的三个判据全证伪（绝对毫秒随机器漂移、冷热比值被同比例变化免疫、规模比被固定基数稀释）；「检索层不变量无覆盖」被穷尽检索推翻（`test_memory_retrieval.py` 早有四个专用用例）；「有 `skipUnless` 就不能登记门禁」被推翻（判据实现是「输出有行首 SKIP」，`OK (skipped=N)` 不命中）；fastloop 两处绝对毫秒阈值假红；fastloop [6] 段的增量判据恒为负。五次同源：**拿规则的字面印象当判据，没读判据的实现**。读实现的成本远低于反复证伪的成本。
5. **恒绿的判据比会假红的判据更危险**。假红吵一次就有人去修；恒绿给人确定的信心，而被测性质其实从未被检验。fastloop [6] 段的增量判据「带/不带 40ms 延迟的停顿差 < 15ms」实测 5 次全为负（-21.87 至 -40.92ms）——注入的阻塞延迟反而「改善」了测量值，因为 `to_thread` 让出循环后心跳更密。它已经这样绿了很久。检出这类问题要**定期看判据的实际输出值**，不只问 pass/fail：一个恒为 -25ms 的「差 < 15ms」和一个恒为 +2ms 的，安全性完全不同。
6. **plumbing 提交后必须逐项验证**。第五十四批用临时索引 + `commit-tree` 落库时，`write-tree` 那次调用漏传 `GIT_INDEX_FILE`，读到被并发 session 污染的主索引，把他们的 12 个文件（2970 insertions）一起提交。`git update-ref` 回退到父提交后重做。plain commit 会明明白白提交整个暂存区（所以一直警惕），plumbing 里一个环境变量漏传会让「我以为只提交了 X」变成「实际提交了 X+Y」且提交前无任何提示。
7. **共享工作区里没有任何 git 信号能区分「谁改的」，只能声明**。第六十一批想给「勿卷入他人文件」建自动检查，两种判据都实测失败：「工作区有未提交改动」会把别人的在途改动算成你的（实测把并发 session 在途的 `run_evals.py` 判成自己的，报干净 exit=0——假阴性比误报更坏）；「本会话提交碰过的路径」也会把别人的提交算成你的（他们用同一本地分支，提交同样落在 `origin/master..HEAD`）。结论是能判定的只有意图，而意图只能由提交者声明。`scripts/check_staged_scope.py` 因此收窄为「暂存区 ⊆ 我声明要提交的路径」，并写进派单宪章的完工标准。工具建立后的第一批就抓到真实污染（并发 session 暂存了删除，准备撤掉自己此前的提交）。
8. **提交信息含反引号或 `$` 时一律走 `-F` 文件**。第六十四批用 `git commit -m` 传含 `` `--allow-skip` `` 的信息，shell 把反引号当命令替换，两处参数名被吃成空档、还打出 `command not found`。文件改动完好，但提交信息残缺，靠 `--amend -F` 修正。改动可以 amend，信息残缺却可能已经在推送后没人发现。

## 五、门禁资产（本会话新增，均 ALWAYS_REQUIRED）

`memory_concurrency`（检索并发不变量，补此前的零覆盖）、`memory_api_offloop`、`voice_offloop`（第四批起覆盖至第二十六批的全部 app.py 异步面）、`router_writes_offloop`（两个 REST 路由器 6 个写端点）、`lessons_cap`、`venv_lock`、`tmp_sweep`。

**第三十九批起的门禁认领**（本地/CI 差距补齐）：`gates_unregistered.txt` 列着只被 CI 的 `for f in scripts/test_*.py` 跑到、本地 `run_gates` 从不跑的脚本，即「本地全绿 ≠ CI 绿」。四批共认领 71 个（`52dd162` `ee8c28b` `9be5bb8` `c811e56`），第五批逐条判定后再认领 12 个（`b03b792`），本地门禁从 51 扩到 158，清单从 114 条降到 8 条。新增 `broadcast_e2e`（跨线程广播链）、`shared_retriever_concurrency`（共享实例并发检索）、`memory_retrieval`（valid-time 降权不变量）。认领的两个前置条件写进了 `docs/TESTING.md`：先 `git ls-files` 确认已跟踪（未跟踪文件登记进去，别人 checkout 后必红），再在 `TEST_SENTINEL=1` 下真跑真过（那才是 `run_gates` 给门禁子进程的真实环境，普通环境 `exit=0` 推断不了）。后者是本会话用 6 个文件的代价换来的——全部同因：`import server.memory.store` 触发 `store.py:1684` 的模块级单例落默认路径，被 #39 哨兵拦在 import 期。

**`memory_fastloop` 判据改造**（第四十八、四十九批）：原 10ms 墙钟阈值因重载假红被 `3afb386` 裁定刻意不进 ALWAYS_REQUIRED，本会话复现两次都按该裁定分流。第四十八批把 [2]/[5] 两段、第四十九批把 [6] 段全部换成**同步对照的心跳数比值**（异步心跳 >= 对照 `BEAT_RATIO` 倍）。改造后判据不再吃机器负载，且判别力经过双向验证（破坏版把 `asyncio.to_thread` patch 成同步执行，三处判据全红；连跑 5 次不假红）。

**判据健康度巡检**（第五十批）：49 个 `.py` 门禁逐个跑并抓完整 stdout，按「数值+单位」筛出带阈值的判据。结论是布尔断言占绝大多数（不存在「离阈值多远」的问题），唯一墙钟类判据（`scheduler_health` 的 scan_due）实测余量 20-40 倍。这个「无发现」证明 fastloop 三处是历史遗留个例，不是系统性缺陷。

**四个既有失败门禁**（全量 191 跑过并断言 / 6 失败，均与本次改动无关，stash 隔离验证过）：`self_evolution_send`、`config_hot_consumers`、`durable_mcp`（缺 `scripts/mcp_servers/echo_server.py`，属并发 session 的 M1-M4 WIP 范围）、`ui_browser`（需 8000 上可交互前端，点击超时）。另两个：`tmp_sweep`（见待拍板项）、`memory_fastloop`（第四十八批前的墙钟假红，改造后已消除）。

**`gate_verdict_r51`**（第六十四批）：钉住 `run_gates` 的完整裁决表。真实门禁覆盖不到这条——`ui_nav_browser` 只在 8000 未起时 SKIP，8000 运行时判 PASS，即「打印 SKIP 行却 exit≠0 必须判 FAIL」这条 R51 本体在真实门禁里根本跑不到。用合成门禁钉四条腿（exit≠0+SKIP行→FAIL、exit≠0无SKIP→FAIL、exit=0+SKIP→SKIP、exit=0无SKIP→PASS）加 `--allow-skip` 不得洗绿真实失败这条边界。第三条腿容易漏：把「环境不满足、没跑」误判成 FAIL 同样危险，会让一条本该跳过的门禁把整套报告染红，训练人忽略红色。判别力验证：把裁决式改回 R51 之前的形态（`skipped` 优先于 `exit`），门禁立刻翻红。

**`check_staged_scope.py`**（第六十一批，刻意**不**进 GATES）：共享仓库里 `git add <我的文件> && git commit` 不等于只提交我的文件——`git commit` 提交的是整个索引。本会话这个形态重复了至少 8 次。脚本把「提交前核对范围」从一句纪律变成一个退出码（暂存区 ⊆ 声明路径）。不进门禁的理由：它是开发时工具，依赖暂存区状态，在门禁子进程里跑没有意义；`scripts/` 下四个 `check_*.py` 先例也都没进。它的位置是派单宪章的完工标准（第六十二批写入）。工具建立后的第一批就抓到真实污染。

## 六、待用户拍板（本会话不擅自做）

1. **#54 迁移交接三项决策**：8000 重启（提交未进 runtime，服务代码冻结在 2026-10-25）、#52 头像退役、#49/#50 挂引擎 #8。
2. **#31 索引拆分批准**：增量索引已实测不可行（TF-IDF `fit+transform` 与 `fit_transform` 非逐位相等，2000 文档 nnz 差 17195），冷热 delta 是固有成本，消除需换检索子系统 + ADR。第三十批与第五十一批两次复验引擎侧均无第二检索连接方，触发条件未到。
3. **#9 toAltar 复跑**：需隔离 8001+5189 实例 + 真实 LLM_API_KEY + ego-browser + dsh worker，Mock 跑不到 toAltar，按「不可为绿而绿」不造假跑。
4. **`tmp_sweep --yes`**：过期项含并发 session 与他人历史工作，批量删除不可逆，等批准。第四十五批修掉了四个浏览器门禁的沙盘泄漏（全量跑一次净增 3-5 个目录），现在超限的构成是修复前攒下的历史残留，不再是新增泄漏；清完过期项后需再跑 `--write` 重置水位。
5. **`.agents/` 入库**（AGENTS.md 待办第 1 条）：`.gitignore:57` 忽略整目录，本会话对 DSH.md 等适配层的修正只在本机。
6. **`durable_mcp` 缺 `scripts/mcp_servers/echo_server.py`**（第五十一批盘点）：`config/agents_roster.yaml` 的 `mcp_echo` 条目与 `config/mcp.json` 的 echo 登记都指向这个文件，但 `scripts/mcp_servers/` 目录整个不存在、HEAD 里也没有。影响面只有 `durable_mcp` 一个门禁（15 条断言里唯一失败的那条）。缺口形态完整（配置、门禁断言、设计文档三样都在，只缺实现），属并发 session 的 M1-M4 WIP 范围，等他们提交即好；若最终不提交，则需在「补一个纯标准库的 stdio JSON-RPC echo server」与「回退 roster/mcp.json 两条目」之间选一个，这个决定属 M1 的 owner。

## 七、共享工作区纪律（本会话的实操约定）

与并发 session 同库工作期间形成：提交前 `git diff HEAD -- <file>` 确认只带自己的 hunk；共享文件用 blob 手术（`git show HEAD:file` → 改 → `hash-object -w --no-filters` → `update-index --cacheinfo`）；手术脚本后绝不补 `git add` 同一文件（会覆盖手术成果，第九批踩过）；索引被并发暂存污染时用临时索引 + `commit-tree` + `update-ref` 落库，再同步自己路径的索引条目。
