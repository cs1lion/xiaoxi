# 事实有效时间（valid-time）设计与验证

日期：2026-10-26。本文是 valid-time 特性的专档；[MEMORY_RESEARCH_NEXT.md](MEMORY_RESEARCH_NEXT.md) 只留摘要与链接。
研究背景：`MEMORY_RESEARCH_NEXT.md` §6.4 把「时间有效性」列为未完成项——"现在在哪工作"与"去年在哪工作"无法区分，当时唯一的时间手段是 inferred/uncertain 的 72h 硬过期，stated 永不过期。

## 1. 字段与命名

memories 表新增两个可空 REAL 列：`fact_valid_from` / `fact_valid_until`。
仅由 `server/memory/temporal.py` 从**显式范围标记**保守解析填入；开放式表达一律留 NULL。

**命名刻意避开 `valid_from` / `valid_until`**：这两个名字属偏好域——`preference_claims.valid_until` 与 `POST /api/interaction/preferences` 的入参。事实域复用会绊倒四处「导出不得含 valid_until」的契约断言（在 `scripts/test_memory_preference.py`、`scripts/test_memory_presentation.py`、`scripts/test_assistant_context.py`、`scripts/test_memory_retrieval.py` 各一处，均搜 `assertNotIn('valid_until'` 可定位；行号会随编辑漂移，以函数名为准）。

`Memory.to_dict()` 显式排除这两个字段，`/api/memories` 响应形状逐字段不变。依据 MEM-OPT-04：向 `/api/memories` 外露新字段需要 `[PROTOCOL_REQUEST]`，而序列化侧排除是默认后果而非可选项。

## 2. 保守解析规则（temporal.py）

只对显式范围标记设界，X/Y 须可解析（绝对 `YYYY年M月D日`/`M月`/`年`，或闭合相对期 今天/昨天/明天/今年/去年/明年/这个月/本月/上个月/下个月/这周/本周/上周/下周）：

| 标记 | 产出 |
|---|---|
| `从X起` / `从X开始` / `自从X` | `fact_valid_from`（区间起点） |
| `到X为止` / `直到X` | `fact_valid_until`（区间终点） |
| `从X到Y` | 两者 |

- **开放式/歧义一律 NULL**：最近、这段时间、目前、现在、裸「这个月」、前段时间。铁律（§3）：不能未经定义就编出具体截止日，不确定时保留原表达与未解析状态。
- **话语标记排除**：`从X说起`/`从X讲起` 不设界（要求 起/开始 紧跟日期）。残余形态 `从X开始说起` 被接受而不加 lookahead——lookahead 会连带误杀合法的 `从X开始说英语`，且误设 `fact_valid_from` 只影响可选审计快照，不影响检索/交付/API。
- **epoch 下界守卫**：`_epoch()` 拒绝负值。1970 前的绝对年份（`从1969年起`）解析为 None——`store.upsert` 拒绝负界，若不守会让 `manager.remember`/`correct` 抛错，且抽取路径无 per-item guard，一个坏候选会丢掉整轮记忆。
- **日期解析用消息时间**：`manager._valid_time` 取 `evidence_message_id` 对应消息的 `created_at`，而非墙钟，避免后台抽取延迟导致「去年/上个月」错位。

## 3. 覆盖率实测

`scripts/probe_temporal_coverage.py`（只读诊断，不进门禁；固定时钟 2026-06-15 UTC，`--json` 可导出完整结果）：

- 显式范围标记的自然措辞 **应捕获 18/18（100%）**（固定时钟 2026-06-15 UTC 实测；数字随时钟与语料变动，以实跑 `probe_temporal_coverage.py` 为准）
- 开放式/歧义/非时间「从到」/话语标记 **误捕获 0/20**

`从2024年3月到6月` 这类区间第二界缺年份的形态**已支持**：显式 `从YYYY年M月[D日]到M月[D日]` 中第二界承继首界年份（无歧义，不存在第二种合理解读）；裸「3月到6月」没有年份上下文，仍保守留 NULL、不猜年份。区间**必须两界都解析成功**，单界越界（`13月`/`2月30日`）整条判未解析，不留半开区间。结论：解析器**校准得当**——宁可漏设不可错设，与「错设 valid_until 会致检索门误排除」的风险相称。

## 4. 各层机制（增量 1–5）

1. **存储**：schema + 迁移 + `Memory`/`RankingRow` 字段 + `RANKING_COLUMNS` 投影；`upsert` 校验（可空、非负、`from <= until`）。真实库曾有旧列，迁移用原地 `ALTER TABLE ... RENAME COLUMN`。
2. **解析**：`temporal.py`。
3. **写入**：`manager._valid_time` 接入 `_persist` 与 `correct`。仅在调用方未提供任一界时才派生，避免把调用方的单侧界与派生界拼成矛盾对。**注意这条分支目前是防御性的、非活跃路径**（2026-10-27 实测）：`_persist` 的唯一调用方 `remember()` 在 `manager.py:288-293` 用硬编码白名单构造 item（kind/subject/source/key/value/importance/confidence/evidence_*），**不透传** `fact_valid_*`，所以手动写入的界只能来自文本派生；`store.upsert`（底层）与 `correct` 路径才接受显式界。别据此以为 `remember(..., fact_valid_from=...)` 能划界——传了会被静默丢弃。
4. **检索（supersession 主路径）**：`search()` 与两个候选查询都准入「`superseded` 且（`fact_valid_until IS NOT NULL` **或** `fact_valid_from IS NOT NULL`）」的行。判别式即用户显式划界——**上下界都算**，因为两者都是用户自己划的时间范围；无界的旧值被 supersede 只是过期旧值，继续排除。**五处准入必须保持一致**：候选 SQL（`store.py` `retrieval_candidates`/`retrieval_ranking_rows`，两处）、`search()` 循环过滤（`retriever.py`）、System-2（`context.py` ContextBuilder）、星图详情路由（`starmap.py` `starmap_node`）。任一处漏改都会重现「图/面板里看得到、检索里取不到」或反之的分裂——星图那处最隐蔽：广告了节点但其详情路由 404，UI 会告诉用户某条记忆"已经不在了"。
5. **交付层**：`present_memory` 增加关键字参数 `allow_historical`（默认 `False`，故 `/api/memories`、星图节点、durable 导出形状不变）；仅模型侧显式开启——`ContextBuilder`（System 2）与 `deliver_context`/`build_context`（System 1 快环）都传 `True`。

   同一参数族后来补了**未来界的镜像** `allow_future`（默认同样 `False`）+ 注入式 `now`：`fact_valid_from` 仍领先于 `now` 的 active 行是「计划」而非现状（"从下个月起…"），降权只改排序、被降权的行仍可能被交付，所以它也要带 `time_note`。`/api/memories` 根本不经过 `present_memory`（走 `Memory.to_dict()`，已显式 pop 两个 `fact_valid_*`），`durable_api` 不传该参数，故导出形状逐字节不变。`historical` 要求 `superseded`、`future` 要求 `active`，两条注释按 status 天然互斥，用 `elif` 保证不会被互相覆盖。`now` 必须注入而非让本模块读时钟：`search()` 因此也接受可选 `now`，`deliver_context` 只读一次并同时喂给排序与标注，避免「排序按墙钟、标注按另一时刻」的分裂。

   **注释措辞不得对整条记录做全称现状断言**（2026-10-26 修）：最初写成「该条自 X 起才生效，**不是现在的状况**」，那是对整条记录的全称断言，而 `fact_valid_from` 只覆盖记录中「下个月起」那部分。对同时含现状与未来的记录（用户真实句式「我现在在乙公司，下个月起去丙公司」），它会命令模型不要把现状当现状——等于让它忽略正确信息，违反「性格与事实准确性冲突时，准确性优先」。已收窄为「该条有{谁}的时间范围：自 X UTC 起才生效；回答现在/目前类问题不要**把它当作已生效的事实**」，只针对界所覆盖的部分。`evals/cases/valid-time-future-delivery-offline.yaml` 用一条 `not_contains: 不是现在的状况` 钉住它，故措辞改回去会直接让黄金门禁变红。

## 5. 软惩罚，不是硬排除

过期行折半降权（`retrieval.validity_expired_factor`，默认 0.50，钳在 [0,1]），而非从检索中移除。理由：「我以前在哪工作」类**历史查询**仍须取到用户显式划定边界的事实，硬排除会让历史查询丢答案。

**未来界对称**：`fact_valid_from` 仍领先于 `now` 的行同样乘该因子。没有这条时，一条未生效的计划会在「我现在…」查询中排到当前事实前面——用户会被告知下个月的安排就是今天的状况。这与上界惩罚是同一因子的两个方向，`config/memory.yaml` 的注释已说明它同时管上下界。

惩罚施加在 `min_score` 门**之后**，因此相关性门用的是未惩罚分数，过期行永不被该门排除。这对 admitted superseded 行同样成立：plan-56 准入的那些「同 key 被 supersede 的未来计划」也吃这个降权，所以它们永远不会盖过同 key 的当前事实。

## 6. time_note 与措辞分级

历史记录带顶层 `time_note`："该条已被后来的陈述取代，只在{你明确划定｜记录中标注}的时间范围内有效（至 {日期} UTC）；回答现在/目前类问题不要采用，仅回答过去相关问题时可用"。与 `value` 同对象，故永不被注入预算裁掉。

**未来界有两种 note，按行的 status 与界的方向区分，互斥不串**：

| 形态 | 触发条件 | 措辞要点 |
| --- | --- | --- |
| active 未来计划 | `status == "active"` 且 `fact_valid_from > now`（`elif allow_future` 分支） | 「该条有{谁}的时间范围：自 {日期} UTC 起才生效；回答现在/目前类问题不要**把它当作已生效的事实**」 |
| superseded 未来计划 | `status == "superseded"` 且只有 `fact_valid_from`（`historical` 分支的 else 臂，plan-56 新增） | 「该条是早前声明的未来安排…同主题后来另有陈述，**是否仍有效未经确认**；回答现在/目前类问题不要采用，仅回答将来计划类问题时可提及并说明未经确认」 |

第二种的措辞刻意**两边都不断言**：既不说计划仍有效，也不说它已失效。supersede 在此是"一个 topic 一个 active 值"键策略的产物，不是用户撤回；断言任何一边都会把未确认信息当事实。`assertNotIn` 在测试里钉住它既不串到历史措辞、也不串到 active 计划措辞。

**措辞按 source 分级**：助手转述被 FMR 降级为 uncertain 的 Claim，措辞用「记录中标注」而非「你明确划定」——用户并未明确说过。准确性优先。

## 7. 两层必须一致（曾经的阻塞级缺陷）

曾只有 `ContextBuilder` 放行历史行，而 `deliver_context` 用默认投影，导致仅历史事实匹配时 `found=False`、persona 注入「你没有任何相关记忆」，而同一轮又在交付该记忆——persona 对用户说谎。已改为两层都放行，`test_only_history_matches_still_reports_found_in_every_layer` 钉住每层都报 found。

相关：`memory_notice` 曾漏 `status` 字段，使前端按 `item.status` 分流的两个分支全成死代码（冲突提示从未显示）。已补字段并由 `scripts/test_voice_turns.py` 的 `test_memory_notice_items_carry_status` 钉住。另外 `orchestrator.py` 的 notice 回调曾被 `except Exception: pass` 静默吞掉——那正是漏字段能潜伏的原因；现改为记录 `_last_meta["notice_error"]`，与紧邻的 `extract_error` 同构。

**准入的两层结构与分歧风险（2026-10-27 补，plan-60 变异自验揭示）**：`search()` 是**共享准入点**——两层的候选集都来自它（`ContextBuilder._memories` 调 `manager.retriever.search(...)` 后在自己的循环里再过一道 status 过滤，`deliver_context` 则直接吃 `search()` 的结果）。所以漂移有两种，红在不同的测试上，别指望一个测试全抓：

- **动共享准入点**（`search()` 循环过滤、`store.py` 两处候选 SQL、`starmap.py` 详情路由）：两层**同时**失去或得到该行，不构成分歧，由检索级测试抓（`ValidTimeSupersessionTests` 的"可达且不越位""星图路由一致"等）。变异自验实测：撤 `search()` 准入后两层均不含该行、分歧为 False。
- **动某一层自己的过滤门**（`context.py` 的 status 过滤、`present_memory` 的 `historical` 判定与 note 分支）：只有该层漂移，**构成真分歧**，由一致性测试抓——`test_future_bound_superseded_row_agrees_across_both_layers` 逐查询比对两层在未来界 superseded 路径上的交付与否、保守 note、当前先于未来的次序。变异自验实测：只撤 `ContextBuilder` 的 `fact_valid_from` 准入即令它变红。

另注意 `allow_historical` 与 `allow_future` 的分工：superseded 行（含只带 `fact_valid_from` 的）走 `historical` 分支、由 `allow_historical` 放行；active 行才走 `elif allow_future`。所以撤掉某层的 `allow_future` **不影响** superseded 路径，只红 active 路径那条测试（`test_future_bound_note_also_reaches_the_fast_loop_block`）——这不是覆盖漏洞，是两条路径本就由不同参数放行。

**一个已核实的理论竞态（不修，记下判据）**：两层的 `now` 各取一次墙钟（`context.py` `build()` 里的 `self._now()`、`retriever.py` `deliver_context()` 里的 `time.time()`），同一轮内相隔毫秒。若某行的界正好落在这两次读取之间，就会一层判「未来界」（降权 + 带 note）、另一层判「已生效」（不降权、无 note），构成 §7 意义上的分歧。实测界的粒度：`from` 界都是整天（00:00:00 UTC），`until` 界落在 23:59:59——两者都是**特定秒**而非毫秒，所以要撞上必须让毫秒级间隔恰好跨过那个秒，单轮概率约「毫秒间隔 / 一日 8640 万毫秒」；且界来自用户给出的日期、非对抗性选取，下一轮两次读取必落在同侧而自愈。故判为理论竞态而非缺陷。**若将来要修**，正解是让调用方算一次 `now` 同时喂给两层（`deliver_context` 已支持注入 `now`，`ContextBuilder.build` 还需加参数），属生产改动，需单独评估——尤其是它会让「两层一致」从构造保证变成依赖调用方传参。

## 8. 已排查的非缺陷（勿重复排查）

- **两层预算机制不同（2026-10-27 复核订正）**：System-1 在**记忆块层**用聚合 `max_inject_chars`（默认 600）；System-2 在 `_memories` 里用 per-record `max_value_chars=320`（超长整条丢弃、不截断），另在 `build()` 里有**上下文级**聚合上限 `MAX_CONTEXT_CHARS=12000`（超限按 `history→inferred→uncertain→stated→active_tasks` 整条弹 items，从不截断 JSON 本身）。原记录称 System-2「无聚合上限」不确——它有聚合上限，只是在上下文层且远高于 System-1 的 600，故默认不触发。

  **默认配置下两层交付量本就不同**（2026-10-27 实测：同库 6 条长记忆，System-1 交付 2 条/567 字，System-2 交付 6 条/2124 字）——原记录称「默认配置下无分歧」不确。窄带内 `found` 也会分歧：记忆值长度落在 (320, 600] 时，System-1 交付并报 found=True、System-2 因 per-record 320 整条丢弃而报 not_found（同日实测：303 字两层一致、333 字分歧、813 字两层均空）。

  **但该分歧无用户可见危害**，因为它到不了 persona：`_render_turn_context` 的 `memory_found` 只取自 System 1（`context_with_status`/`build_context`，其 `long_term_memory` 由快环 `_memory_records` 产出），而 System 2 的 `long_term_memory` 在助手慢环只被 `service.py` 用来取 `used_ids` 记账、不读 status；`not_found` 在助手链路上也无任何一处转成负向约束。故这仍是预算策略差异，不是上述缺陷那类的准入规则不一致——但"无分歧"的旧表述是错的，别据此跳过排查。
- **durable/action 路径看不到历史事实**：`server/assistant/durable_api.py` 仍过滤 `status != 'active'`（`:78`），且 `:89` 调 `present_memory` 时**不传** `allow_historical`（默认 False），故历史行即便混入也会被 `historical` 判定挡掉。它不是自相矛盾——该路径有独立负向约束兜底（同文件里的「记忆检索暂不可用；没查到不等于没发生，不要补写」），失败模式保守。且该文件长期处于并发会话未提交状态，本线不碰。若将来 durable 路径要答过去类问题，再单独评估。

  **定位信息（2026-10-27 补）**：它是 plan-56 放宽的**六处准入点里唯一没跟进的一处**（其余五处：`store.py` 两处候选 SQL、`retriever.py` 循环过滤、`context.py` ContextBuilder、`starmap.py` 详情路由）。将来若统一收口，别漏了这一处。**数准入点时注意**：`context.py` 的写法是 `_field(record, "fact_valid_from") is not None`，用 `grep "fact_valid_from is not None"` 会漏掉它、把五处数成四处——这是实测踩过的坑，核这类条件要用 AST 或多种写法分别匹配。
- **同 key 的未来计划曾被永久排除（2026-10-26 已修复，勿再按旧结论排查）**：准入规则原本只放行「`superseded` 且 `fact_valid_until IS NOT NULL`」。而部分唯一索引 `idx_mem_identity_active ... WHERE status='active'`（`store.py`）与抽取侧"一个 topic 一个 active 值"的键策略，使同 key 的第二次写入必然把前一条置为 `superseded`。于是用户先说「从下个月起去丙公司」、后说「我现在在乙公司」（同 key）时，那条未来计划变成 `superseded` 且 `fact_valid_until` 为 NULL，**再也取不回来**——valid-time 因此能表达一个 topic 的过去，却无法表达与现状并存的未来。

  **这不是用户撤回**：「我现在在乙公司」并不撤回「下个月起去丙公司」，两者在不同时间都成立；supersede 是键策略的产物。且准入规则的本意是"用户显式划界的 superseded 行值得保留"，界本该上下界都算——只认上界是原则漏洞。

  修法（2026-10-26）：**五处准入**（`store.py` 两处候选 SQL、`retriever.py` 循环过滤、`context.py` ContextBuilder、`starmap.py` 详情路由）统一改为「`superseded` 且（`fact_valid_until IS NOT NULL` 或 `fact_valid_from IS NOT NULL`）」；`presentation.py` 的 `historical` 判定同步扩展，并在 note 分支里按界的方向区分——`until` 走既有「已被后来的陈述取代」措辞，`from`-only 走**新的保守措辞**："该条是早前声明的未来安排…同主题后来另有陈述，是否仍有效未经确认；回答现在/目前类问题不要采用，仅回答将来计划类问题时可提及并说明未经确认"。措辞刻意**两边都不断言**：既不说计划仍有效，也不说它已失效——断言错任何一边都会造出"把未确认信息当事实"的错误。

  **为什么早先"走不通"**：那次只改了三处准入，漏了 `context.py` 与 `starmap.py`，且 `present_memory` 的 `historical` 仍只认 `fact_valid_until`，于是 `from`-only 的 superseded 行在交付层被直接丢弃。真正修必须**五处准入 + `historical` 判定 + note 分支**一起改，任一处漏改都会重现分裂。`scripts/test_memory_retrieval.py` 用两类用例分别钉住：`ValidTimeSupersessionTests`（5 例：有界历史事实仍可达且不越位、无界旧值仍排除、include_done 语义不变、同 key 未来计划可达且不越位、星图详情路由准入一致）管检索与路由；`ValidTimeDeliveryTests.test_same_key_superseded_plan_gets_a_conservative_note` 管交付层保守 note。六个变异（逐一撤回各处准入与 `historical` 判定）全部被抓住。

## 9. live 验证

`evals/cases/valid-time-history-live.yaml`（AGENTS.md 要求「改 Prompt 装配逻辑后跑一次 live」）3/3 轮通过——模型确实把「到去年为止在甲公司工作」作为过去呈现、未误作现状。全量 live 层 12 passed / 0 failed / 0 review。检索与装配层另有 `evals/cases/valid-time-history-delivery-offline.yaml` 在黄金门禁层钉住（无需 key）。

> **待复验（2026-10-26 追加，勿当作已通过）**：后来补了**未来下界**的对称降权（`fact_valid_from > now` 时同样乘 `validity_expired_factor`，见 `server/memory/retriever.py` 检索循环）。它改变了进入上下文块的记录集合与次序，按 AGENTS.md 属于「改 Prompt 装配逻辑」，**必须重跑一次 live**。五次尝试均被供应商/网络侧挡住，与本次改动无关（这些改动都是纯 Python 检索/装配逻辑，零网络调用）：第 1、2、5 次是 `HTTP 429`，第 3 次是 `LLMError: request_failed:ConnectTimeout`，第 4 次连错误都没拿到（`--live` 跑满 600 秒被 kill、日志 0 字节）。第 3 与第 5 次都卡在**第一个 live 用例** `accuracy-over-persona-live` 的首轮（`scripts/run_evals.py` 的 `run_live_turns` 里那次 `llm.complete_raw`，rescue 重试同样失败），0 个 PASS——说明连改动之前就存在的用例都跑不起来，阻塞在环境而非本线代码。注意第 4 次的"0 字节"**不能**用来定位卡点：Python 的 stdout 重定向到文件时是块缓冲，离线用例的打印行可能已产生只是没 flush，所以"启动即卡"不成立，只能说整条命令在 600 秒内没有正常结束。离线层已先行兜住：`valid-time-history-delivery-offline.yaml` 在黄金门禁层 10 passed，且 `test_memory_retrieval.py` 新增 3 例（未来降权 / NULL 不动分 / 未来界仍可达）并用变异自验非空洞。429/超时消退后补跑 `DB_PATH=:memory: PYTHONUTF8=1 .venv/Scripts/python.exe scripts/run_evals.py --live`，通过后删掉本段。

> **2026-11-05 复跑实况（勿把上面的 429/超时当成全部挡法）**：本轮单独按 `--live --case valid-time-history-live --max-calls 20` 复跑，三轮全部 `LLMError: HTTP 400`。用最小 `/chat/completions` 请求复核，根因是 `https://api.stepfun.com/step_plan/v1` 返回 `{"error":{"message":"you have no active step plan subscription","type":"request_params_invalid"}}`——**step_plan 订阅未激活**，不是限流、不是网络、也不是本线代码。换 `LLM_BASE_URL` 等于换模型，不能算这次改动的复验（会污染「同一模型同一 Prompt」的口径），所以没有绕过，也没有把它记成通过。离线层仍由三条 `valid-time-*-delivery-offline.yaml` 与 `test_memory_retrieval.py` / `test_memory_presentation.py` 守着装配与排序，缺的只有「真实模型是否照着做」。恢复条件：订阅激活或拿到与当时同模型的可用端点，再跑上面那条命令；通过后把 :111 与 :119 两段一起删掉。

> **同一次改动的延续（2026-10-26，合并进上一段而非新开）**：随后又做了四件事，都属同一条「改 Prompt 装配逻辑」的链，**同样需要那次 live**——
> 1. `present_memory` 增加 `allow_future` + 注入式 `now`，未来界的 active 行也带 `time_note`；
> 2. 发现并修掉注释措辞的全称断言缺陷（原「不是现在的状况」会对同时含现状与未来的记录下令忽略真实现状，已收窄，见 §4 第 5 项与 §6 表）；
> 3. 新增离线用例 `evals/cases/valid-time-future-delivery-offline.yaml` 与检查函数 `evals.checks:current_before_future`，黄金门禁层离线从 10 passed 增至 **11 passed**；
> 4. **plan-56 放宽五处准入**：同 key 的未来计划（被 supersede 且只带 `fact_valid_from`）重新可达，并带新的保守措辞 note（见 §8 第三条与 §6 表第二行）。
>
> 四者均已有离线/单测层守护（新用例 + `test_memory_presentation.py` 的 `FutureProjectionTests` + `test_memory_retrieval.py` 的两条集成用例与 `ValidTimeSupersessionTests` 的四条），并做过变异自验：措辞改回宽断言、交付层稳定分区失效、撤掉 `allow_future` 接线、逐一撤回五处准入（共六个变异），均令新用例或新测试变红。**缺的只有 live**：上述改动都改变了最终 Prompt 的文本内容与记录集合，按 AGENTS.md 必须在 live 层确认模型确实不再把未来计划当现状回答。429/超时消退后跑同一条命令，通过后把这两段一起删掉。

## 10. 提交注意

`server/memory/temporal.py` 未被 git 跟踪，而 `manager.py` 已修改并 `from .temporal import temporal_bounds`。**两者必须原子提交**，否则单独提交 manager.py 会令整条 `server.memory` 导入链 `ModuleNotFoundError`。HEAD 自身不含该 import，故 clone HEAD 不会坏；这仅是工作树的提交顺序约束。
