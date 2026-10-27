# LATENCY BASELINE — 语音快环三指标基线（S1 · issue #27）

**基准锚点：HEAD `94b1abe`，2026-10-24（运行时间戳 12:23:51 +08:00），Windows 11 本机回环，Python 3.12.4。**

数字全部来自该锚点下的单次完整运行（`tmp/bench-voice-loop-20261024-122351/result.json`），
呈现为「分布 + 样本数」；单次数字不是基线，引用必须连锚点一起抄。

## 这测的是什么、不是什么

- **是**：Mock 链路开销。LLM 走 Mock（无 Key 自动 offline），TTS 用基准桩（确定性离线 WAV，
  220Hz 正弦 0.12s ≈ 5.8KB/块，零网络零延迟）。orchestrator／记忆检索／交付器／WS 全部是生产代码。
- **不是**：真实用户延迟。live 的 LLM/TTS/ASR 数值另行手动跑（见文末「live 待办」）。
- 放在 `docs/` 而非 `evals/` 的理由：`evals/` 是 Role 0 的回归用例目录（接线 `run_evals.py`
  与 CI，用例是 pass/fail 判定）；这是一份带时间锚的测量记录，不是回归用例。PARKED_TASKS §B
  「End-to-end … round-trip latency (exit gate …)」条目的程序侧补课即指本文。

## 复跑

```bash
.\.venv\Scripts\python.exe scripts\bench_voice_loop.py            # 全量（A/B/C，约 2 分钟）
.\.venv\Scripts\python.exe scripts\bench_voice_loop.py --only c   # 只要记忆检索
```

脚本自起自收独立端口真实服务（默认 **8031**，`--port` 可改；**拒绝 8000/8010**），
Mock 模式；指标 B 单独起一个 `BENCH_TTS_DELAY_MS=250` 的实例（给合成留一个在途窗口，
否则 Mock 链路毫秒级收轮、打断永远追不上）。记忆库是临时 DB（三档锚点 + 无关填充），
不碰 `data/memory.db`。指标 C 在进程内直呼 `MemoryRetriever.build_context()`，
选这条路是因为可重复、可解释（见票面），且与生产调用同形（`mark_used=False`）。

## 指标 A · 快环首字延迟（text turn 发出 → 首个 audio 块）

语料 50 条，n=40，客户端侧计时（含 WS 往返），单位 ms：

| 分段 | p50 | p90 | p95 | max | mean |
|---|---|---|---|---|---|
| turn→asr 回显 | 1.0 | 1.4 | 1.5 | 2.3 | 1.1 |
| turn→thinking（含 `_turn_context` 检索+装配） | 35.3 | 40.9 | 42.6 | 60.2 | 36.5 |
| turn→首个 text_delta | 94.3 | 95.6 | 107.1 | 117.8 | 95.6 |
| **turn→首个 audio** | **95.0** | **96.1** | **108.0** | **118.5** | **96.3** |
| turn→done（收轮） | 136.2 | 138.9 | 148.5 | 162.0 | 136.3 |

服务端自报 `done.latency_ms`（不含客户端→服务端传输）：p50 136 / max 157（n=40），
与客户端观测差 ≈3ms，回环传输开销可忽略。每轮 1 个音频块（mock 问句回复 12 字）。

**解读（Mock 口径的构成）**：首字 ≈ 36ms 轮次装配（检索 + context broker + limit_context
测量回调 + 历史/入库 SQLite 写）+ 约 59ms Mock LLM 流式节奏（桩固定 6 字/20ms，首句 ≥8 字
需 2 个 delta）+ ~1ms 合成桩与出帧。**约六成首字时间是 Mock 桩自带的节奏，不是管线开销**；
live 口径里这段会被真实模型首 token 时间替换（量级更大），装配段 36ms 才是管线自己的账。
text_delta→audio 仅 ~0.7ms：微句切分→合成→出帧的管线部分在此配置下接近零成本。

**可重复性**：同参数三次完整运行首字 p50 = 94.8 / 95.4 / 95.0 ms。

## 指标 B · 打断响应（barge_in 发出 → playback_stopped 到达）

TTS 桩 250ms/块（在途窗口），n=30：

| 分段 | p50 | p90 | p95 | max |
|---|---|---|---|---|
| barge_in→interrupted | 0.46 | 0.59 | 0.63 | 0.69 |
| **barge_in→playback_stopped** | **0.66** | **0.93** | **0.95** | **0.99 |

命中率 30/30（无「turn 先收轮」miss）；打断后 300ms 静默窗口内旧 turn 的
audio/text_delta 违规 **0** 条（协议 §十一的「打断后不再发旧轮 audio」实测成立）。

**解读**：取消路径（`Connection.cancel`：revision 栅栏 + task 取消 + 两帧 await send）
是亚毫秒级，且在合成 await 悬挂期间同样亚毫秒——打断慢不会来自服务端取消逻辑本身。
**口径限制**：本指标只覆盖文本轮（text turn）发起的打断；语音流（start/audio_end 途中、
ASR 等待中）的打断未在此基准内（ASR 在 Mock 模式无意义），live 补测时需另列。

## 指标 C · 记忆检索耗时（三档置信度装配路径，进程内）

`build_context()`，与生产同形；「冷」= 记忆集变化后的首次检索（TF-IDF 全量重拟合），
「热」= 索引已缓存；分档桶各 30 次且断言命中的确是目标档（30/30）：

| 桶 | 50 条 p50 | 50 条 p90/max | 300 条 p50 | 300 条 p90/max |
|---|---|---|---|---|
| 冷（索引重建） | 4.5ms (n=8) | 22.9 / 22.9 | 19.8ms (n=8) | 22.4 / 22.4 |
| 热（缓存命中） | 1.8ms (n=60) | 2.3 / 2.6 | 6.9ms (n=60) | 8.3 / **48.9** |
| stated 档查询 | 1.7ms | 1.9 / 6.3 | 7.3ms | 8.2 / 24.3 |
| inferred 档查询 | 1.8ms | 2.2 / 4.9 | 7.4ms | 9.0 / 19.6 |
| uncertain 档查询 | 1.9ms | 2.4 / 4.8 | 6.8ms | 8.4 / 20.5 |
| 无命中（负向路径） | 1.7ms | 2.2 / 2.5 | 7.8ms | 9.0 / 9.6 |

**解读**：三档本身不构成耗时差异（同一检索路径，档位只影响得分乘子）；规模才是变量——
检索成本随库大小线性走（50→300 热路径 ×3.8，冷路径 ×4.4）。热路径 300 条 p50 6.9ms
可接受；corpus_300 热路径各有一次 46.8ms / 48.9ms 离群（两次完整运行各一次，量级稳定，
p90 仅 8.3ms，疑为周期性 GC 尖峰；作为已知尖峰记录，不影响 p90 口径）。**在 A 的 36ms
装配段里，检索（50 条库）只占约 2ms**，大头是装配周边（context broker、limit_context
反复测量、历史读写）——优化装配时应先看那里。

## 卸载批次的首字延迟受控 A/B（2026-10-27 · 第十一至二十三批离环 sweep）

**锚点：2026-10-27 本机回环，`scripts/bench_voice_loop.py --only a --samples-a 40`（corpus=50，port 8031，Mock LLM + 离线 WAV TTS 桩）。** 两组跑同日同时段、同 `.venv`、同机器；JSON 落 `tmp/bench-voice-loop-20261027-*`（TTL 内，引用连本锚点一起抄）。

背景：第十一至二十三批把 app.py 各路径的同步 SQLite 逐一卸载到 `asyncio.to_thread`（共 20+ 处）。MEM-OPT-01 验收口径（上节）预期「首字不变化」，但那是推理，本段给它同时段同机器的受控数据。

| 代码状态 | commit | 首字 p50 | p90 | p95 | max | thinking p50 | text_delta p50 |
|---|---|---|---|---|---|---|---|
| 会话前（卸载前） | `b7b7136` | 109.8ms | 111.3 | 111.8 | 205.3 | 41.3ms | 108.9ms |
| 卸载后（24 个提交） | 工作区 | 108.4 / 108.8ms | 110.6 / 111.1 | 110.7 / 112.0 | 169.5 / 170.2 | 41.2 / 41.5ms | 107.8 / 108.0ms |

**解读**：两组差异在噪声内（p50 相差 1.0-1.4ms，而各自日内重复性 ±0.4ms、max 抖动 ±35ms）。**结论：卸载批次对首字延迟零影响**，MEM-OPT-01 的「不预期首字变化」首次有受控数据支撑。

与本文开头 2026-10-24 基线（p50 95.0ms）对比的 +13ms **全部归因于机器/环境漂移**，不是代码：会话前代码同日同时段同机器同样跑出 109.8ms。三天累积的后台负载、依赖更新、Windows 状态足以解释该差值；引用旧基线数字时必须连本段一起读，否则会把环境漂移误判成回归。

方法论点：跨天比延迟数字等于把环境和代码混在一个量里。本轮用 `git worktree add` 到会话前父提交做同时段对照，是把混淆项分离的最低成本手段（worktree 用完即 `git worktree remove --force` 清理）。

## 写提交与读的耗时分野（2026-10-27 · 第十一至十六批离环 sweep）

**锚点：2026-10-27 本机，临时 SQLite 库（1000-5000 行），`.venv` Python；一次性探针，n=30-50，worst 或 median/max。** 探针已删，引用连本锚点一起抄。

第十至十六批把 app.py 的同步 SQLite 逐一卸载出事件循环，分界判据从「读耗时 vs to_thread 调度开销」演进到「写提交 vs 读」：

| 调用 | 形态 | 实测 |
|---|---|---|
| store.count() | 读（覆盖索引） | 1000 行 0.34ms / 5000 行 2.16ms |
| manager.snapshot(200)+count | 读（建 200 对象） | 19.39ms |
| manager.context_with_status | 读（TF-IDF 装配） | 75.73ms |
| manager.history_of | 读（无 key 索引，全表扫描） | .46ms@1000 行，O(表长) |
| store.recent_messages(100) | 读（命中 idx_msg_session） | 0.31ms |
| executive.pending_reports | 读（BEGIN IMMEDIATE 无脏页） | 0.22ms |
| executive.accept | **写提交** | worst 9.42ms |
| dispatcher.cancel | **写提交** | worst 12.02ms（第十三批） |
| spend_spark | **写提交** | median 9.05 / max 12.12ms |
| manager.set_disabled | **写提交** | median 3.97 / max 10.93ms |
| to_thread 调度（空函数） | — | worst 0.248ms |

**机制**：`MemoryStore._transaction` 是 `BEGIN IMMEDIATE` + `commit()`，默认 `synchronous=FULL`，每个写提交一次 fsync（`store.py:1043` 注释印证量级）；读不脏页，commit 近乎零成本。结论：**写一律卸、不问实测**；亚毫秒的有界索引读留同步。

**一次测量事故与纠正**：第十五批把 set_disabled 判为 0.50ms 保留同步，那是「反复拨同一行」探针的缓存快速路径假象（页与 WAL 尾部皆热、只脏同一页，fsync 代价被摊薄）。第十六批逐行翻一次复测得 median 3.97ms，补卸。两条教训：判据不该贴着阈值（0.50 vs 0.248 只有 2x 余量而实测误差 8x）；「不修」的结论比「修」的结论更需要证明测的是真路径（第十四批同款事故：探针文本被正则提前截断，量的不是目标分支）。

**残余限界**：store 是单连接 + 单 RLock，保留同步的轻读若撞上已卸载的写正持锁，会阻塞在 RLock 上连带卡住循环；CommissionStore 的 `_transaction` 是 BEGIN IMMEDIATE（读也拿写锁），pending_reports 同样暴露在这个争用面下。

## 发现与立案（零静默）

| # | 发现 | 处置 |
|---|---|---|
| F-1 | 无关中文查询假命中：sim 0.35、`found=True`、注入 3 条无关记录；负向约束分支难以触发 | **issue #30** |
| F-2 | 索引重建同步发生在快环 turn 路径，随库规模线性增长（300 条 ≈20ms，外推 3k 条 ≈200ms） | **issue #31** |
| F-3 | Mock LLM 桩按 6 字/20ms 吐字，占首字时间约六成；A 的 p95≈108ms 台阶即其量化 | 记录在案，Mock 口径解读必读，不立案（桩行为） |
| F-4 | 闲聊兜底记忆卡（aqi_blocks）晚于首个 audio 到达、先于 done | 契约只要求「先于 done」（§23.4），实测合规，不立案 |
| F-5 | corpus_300 热路径离群尖峰：两次完整运行各一次（46.8 / 48.9ms），p90 仅 8.3ms，量级稳定疑为周期性 GC | 记录在案（上文）；量级不伤 p90，暂不立案 |

本票不修任何卡顿点（票面约定）；F-1/F-2 已开票待认领。

## live 待办（不在本基线内）

真实 LLM/TTS/ASR 的 live 数值需 `LLM_API_KEY` 手动跑（口径：同一脚本对 live 服务实例重复；
脚本已把 provider 桩参数化，live 复测时 `BENCH_TTS_DELAY_MS=0` 且不注入 mock 回复即可）。
PARKED_TASKS §B 的「真实麦克风/扬声器声学回路延迟」属于硬件在环，仍留在停车场。
