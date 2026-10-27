# 决策记录：2026-10-27 快环阻塞调用卸载 sweep（8 小时自主迭代）

作者：Chief（poteto-mode A 档）｜日期：2026-10-27｜会话形态：ultracode 8 小时不间断迭代，方向与决策自主。

本档是 31 个提交的决策 trail 提炼版，完整版（含每次探针的原始数据、每次 review 的逐条结论、每次共享文件手术的细节）在本机 `tmp/ITERATION_LOG.md`（gitignored）。本档只留**需要跨会话存活的部分**：判定、触发器、待拍板项。

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

另有 `1b993cf`（会话前既有提交）与 `abe6bd9`/`3afb386`（文档入库与门禁裁定）。

每批均过四道黄金门禁（`check_protocol.py` EXIT=0、`test_core.py` 92 passed、`run_evals.py` 7→9 passed、`npm test` EXIT=0）+ 本批新增/扩展门禁 + subagent review。

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

## 五、门禁资产（本会话新增，均 ALWAYS_REQUIRED）

`memory_concurrency`（检索并发不变量，补此前的零覆盖）、`memory_api_offloop`、`voice_offloop`（第四批起覆盖至第二十六批的全部 app.py 异步面）、`router_writes_offloop`（两个 REST 路由器 6 个写端点）、`lessons_cap`、`venv_lock`、`tmp_sweep`。

判据全是线程身份或行为不变量，不是墙钟——`memory_fastloop` 的 10ms 墙钟阈值因重载假红被 `3afb386` 裁定刻意不进 ALWAYS_REQUIRED，本会话复现两次（第二十四批全套跑时红、单独跑绿），每次都按该裁定分流，不当回归处理。

## 六、待用户拍板（本会话不擅自做）

1. **#54 迁移交接三项决策**：8000 重启（15+ 提交未进 runtime，服务代码冻结在 2026-10-25）、#52 头像退役、#49/#50 挂引擎 #8。
2. **#31 索引拆分批准**：增量索引已实测不可行（TF-IDF `fit+transform` 与 `fit_transform` 非逐位相等，2000 文档 nnz 差 17195），冷热 delta 是固有成本，消除需换检索子系统 + ADR。
3. **#9 toAltar 复跑**：需隔离 8001+5189 实例 + 真实 LLM_API_KEY + ego-browser + dsh worker，Mock 跑不到 toAltar，按「不可为绿而绿」不造假跑。
4. **`tmp_sweep --yes`**：346 项过期项含并发 session 与他人历史工作，批量删除不可逆，等批准。
5. **`.agents/` 入库**（AGENTS.md 待办第 1 条）：`.gitignore:57` 忽略整目录，本会话对 DSH.md 等适配层的修正只在本机。

## 七、共享工作区纪律（本会话的实操约定）

与并发 session 同库工作期间形成：提交前 `git diff HEAD -- <file>` 确认只带自己的 hunk；共享文件用 blob 手术（`git show HEAD:file` → 改 → `hash-object -w --no-filters` → `update-index --cacheinfo`）；手术脚本后绝不补 `git add` 同一文件（会覆盖手术成果，第九批踩过）；索引被并发暂存污染时用临时索引 + `commit-tree` + `update-ref` 落库，再同步自己路径的索引条目。
