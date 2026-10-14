# 语音搭档：复杂事项与记忆实践

2026-10-13。回应用户对产品主线的修正，结合当前源码和本日重新读取的一手资料。本轮只整理研究和路线，不改产品代码、读取真实用户数据、调用付费模型、采集麦克风或重启服务。

后续执行更新：用户要求继续后，[组合记忆第一增量](MEMORY_CONTEXT_DELIVERY.md)已实现共享上下文及有效候选/索引修复。下文保留当时研究与改动前诊断，行动助手遗漏限定的缺口现已修复；项目范围和完整时间等后续设计仍未实现。

## 1. 产品价值与适用任务

阿栖的核心价值是：一个有声音、有性格，能陪用户把尚未成形的想法逐渐想清楚，并在下一次接着讨论的个人搭档。语音的节奏、倾听、犹豫、打断与接续共同创造氛围；判断能力和可靠记忆让共同讨论产生实际进展。

“市场上多数 agent 不用语音”没有在本轮做统计。语音 agent 已经存在，项目的差异化应通过用户是否愿意开口、能否自由改主意、讨论是否有进展、下次能否接住来验证。

“一起做”优先面向具有共同讨论价值的任务：信息尚不完整、选项有取舍、目标会调整、作品需要反复修改、过程跨越多次会话。复杂度来自认知负荷，不只来自步骤或工具数量；共同思考、写作和学习都可以成立。

简单问题、改一句话或设一个提醒直接完成。短小交流也可以有语音陪伴价值，只有需要留下共识或跨日接续时才引入共同事项。语音用于讨论和简短反馈；长资料、方案比较和稿件版本放到可阅读的载体。

## 2. 本次发现：抽取正确仍可能记错

首轮真实抽取中的原话是“长期最喜欢绿茶”，随后“最近红茶也挺喜欢，还没决定改习惯”。公开模型输出没有声称红茶替代绿茶，旧程序却把两条同属性信息判为冲突。这说明错误可以发生在保存与更新决策，而不只在模型抽取。

现有[偏好契约](MEMORY_PREFERENCES.md)已修复有限句型中的并存、程度、范围及迟到退役顺序；旧输出已离线重放。完整时间过滤、改后提示词真实稳定性及回答如何使用限定仍未完成。

对复杂事项，类似错误会更影响体验：“讨论过方案 A”不等于“决定采用 A”；“当前尝试 B”不等于“以后一直用 B”；“创建了任务”不等于“目标已完成”。记忆应保留这些差别，以及决定依据和成果版本。

## 3. 一手实践：借哪一层

没有适合所有应用的单一最佳记忆架构。以下比较的是本日所查版本的机制与项目适配判断，没有复现各家的成绩，也不是市场覆盖率或效果排名。

| 来源 | 当前核验到的做法 | 本项目适合借鉴的部分 |
| --- | --- | --- |
| [LangGraph 记忆概览](https://docs.langchain.com/oss/python/concepts/memory.md) | 区分线程内上下文和跨会话命名空间；讨论事实集合与单一画像、即时写入与后台整理的取舍 | 按会话/事项/长期事实区分范围。当前对话直接使用近期原话，较慢整理在后台进行；显式保存只有落库成功才反馈成功 |
| [Mem0 当前 Add API](https://docs.mem0.ai/api-reference/memory/add-memories.md) | V3 自动抽取使用单次 LLM、ADD-only；仍提供显式 update/delete。当前 OSS 会检索旧记忆与近期消息、抽取、去重并追加 | 保留原始证据与独立事实，避免粗粒度覆盖。以受控对照比较写入时维护当前状态与读取时解释变化；继续保留纠正、删除及迟到写入守卫 |
| [Graphiti 官方概览](https://help.getzep.com/graphiti/getting-started/overview.md) | 原始事件和事实边有来源关联；事实有效时间与系统处理时间分开；变化时失效旧事实并保留历史。支持多种混合检索配置 | 先在 SQLite 中补足时间、来源和修订关系。查询“现在”“之前”“为什么改”应选择不同证据；关系图等到关联检索失败有明确证据再考虑 |
| [Letta 当前 MemFS](https://docs.letta.com/concepts/memfs/index.md) | 根目录文件每轮常驻，深层目录按需读取；旧版本使用 system/。默认没有语义/向量索引，并允许 agent 使用文件工具编辑记忆 | 少量稳定信息常驻，当前事项按需展开，旧资料按需读取。沿用程序管理的证据和权限；不直接赋予角色自行改写用户事实、人格或授权的能力 |

### Mem0 的版本边界

Add API 的原句是 “single-pass ADD-only extraction: one LLM call, no UPDATE/DELETE.” 这里指自动提取路径。[显式更新](https://docs.mem0.ai/core-concepts/memory-operations/update.md)和[显式删除](https://docs.mem0.ai/core-concepts/memory-operations/delete.md)仍在。一次抽取调用也不等于整个流程只有一次网络请求，embedding 和可选重排另有工作。

当前 OSS 固定提交为 `d26ba5e4518cef7fff3c66d4f715b21adf9feabe`。已读取[追加实现](https://github.com/mem0ai/mem0/blob/d26ba5e4518cef7fff3c66d4f715b21adf9feabe/mem0/memory/main.py#L918)、[检索实现](https://github.com/mem0ai/mem0/blob/d26ba5e4518cef7fff3c66d4f715b21adf9feabe/mem0/memory/main.py#L1642)和[抽取提示](https://github.com/mem0ai/mem0/blob/d26ba5e4518cef7fff3c66d4f715b21adf9feabe/mem0/configs/prompts.py#L472)。OSS 当前拒绝 `add(timestamp=...)` 与 `search(reference_date=...)`；创建/修改时间及到期隐藏不能视为完整事实有效时间。托管版能力与 OSS 分开判断。

该提示也处理助手内容和模型生成的归属。阿栖的角色表达不能因此变成真实共同经历或用户事实；工具结果继续使用独立的真实回执来源。自动只追加与自动维护当前状态都需要正确准入、来源、检索和用户维护，不能仅凭 ADD-only 宣称已经解决记忆问题。

### Graphiti 的时间与溯源

固定提交为 `2f0fb35b749f1ff17377892e099882176e453393`。[README](https://github.com/getzep/graphiti/blob/2f0fb35b749f1ff17377892e099882176e453393/README.md#L119)明确 “When information changes, old facts are invalidated — not deleted.” 这是事实变化的处理，不能解释为用户没有删除权。

已读取[事件节点](https://github.com/getzep/graphiti/blob/2f0fb35b749f1ff17377892e099882176e453393/graphiti_core/nodes.py#L318)、[事实边](https://github.com/getzep/graphiti/blob/2f0fb35b749f1ff17377892e099882176e453393/graphiti_core/edges.py#L265)及[检索配置](https://github.com/getzep/graphiti/blob/2f0fb35b749f1ff17377892e099882176e453393/graphiti_core/search/search_config_recipes.py#L33)。事件何时发生、事实何时有效、系统何时处理分别表达；来源事件关联用于追溯。BM25、向量和可选图遍历可以组合，重排也有不同成本。Zep 托管平台的承诺不等于本项目自建 Graphiti 的结果。

### Letta 的层次与管理边界

当前 MemFS 说明：“Files at the memory root are loaded into the agent’s system prompt on every turn.” 深层目录则 “stay out of context until they are needed”，并明确默认没有语义或向量索引。

[SDK Memory](https://docs.letta.com/agent-sdk/memory/index.md)仍描述旧 system/ 目录及 “the agent edits it as it learns”；[权限说明](https://docs.letta.com/configuration/permissions/index.md)提供工具审批模式、allow/deny 与目录保护。这些机制没有给出本项目所需的事实级证据、迟到纠正或删除传播保证。借鉴分层和版本历史即可，不必迁移存储，也不把当前全部机制等同历史 MemGPT 的 blocks/archival 设计。

## 4. 对本项目的最小适配

### 提取：分清内容角色，再整理

保留原用户消息、原话片段和时间。提取候选应区分个人事实、当前探索、已确认决定、待定问题、进展和下一步；助手建议只有在用户确认或产生真实回执后才有对应权威。沿用现有事项候选确认，不把普通闲聊自动转成事项。

共同讨论尤其要保留“为什么”，包括采用与放弃选项的依据。“为什么没选 A”通常需要原讨论和决定关系，单独的静态用户偏好不能回答。会话摘要可帮助导航，结论仍应落回原话、确认记录或资料版本；摘要不能升格未决意见。

后台整理减少语音等待，但带来可见延迟和旧任务晚到风险。刚说过的话先从当前会话使用；保存请求以实际落库为准。现有消息顺序、删除和关闭守卫继续适用。

### 保存：小核心、事项工作上下文、可追溯原记录

- 少量稳定信息：明确相处偏好和与当前请求有关的长期事实；角色与权限另行维护。
- 当前事项：目标、决定及理由、未决问题、当前稿/资料版本、下一步，以及可验证的执行结果。
- 原记录与归档：聊天证据、修订历史、已保存资料和回执。摘要及索引是可重建的派生物。

记录身份应包含对象/属性/范围，允许独立信息并存；时间区分原消息时间、写入时间和已知事实有效时间。“最近”保留原表达及未解析状态，不猜固定过期日。

确认的决定应有持久来源关系；资料引用使用 type/id/version/用途。先做 SQLite 关联集合，延续现有笔记版本和个别成果回执。用户修正或删除后，引用、派生摘要、索引与待写任务应响应；跨域遗忘目前仍是缺口，不能用单表删除代替。

### 检索：先归属和时间，再相关性

建议读取顺序为：当前对话与明确材料 → 当前事项的状态、相关事件和资料 → 与本轮有关的长期事实 → 必要时查旧记录。当前记录与历史查询使用不同视图；没有明确事项时，沿用选择/澄清入口。

先按事项、来源、状态、时间与版本约束搜索空间，再做词法或语义排序。相关性高不意味着当前有效。对“最近也喜欢红茶”，回答需要同时理解对象、程度和时间范围，不能只因长期绿茶更常被提到就选它。

下一步可将“摘要召回 + 展开相邻原记录/资料版本”作为对照方案；只增加与问题有关的邻近证据，保留预算与来源。若原记录没有答案，则明确缺失，不编造共同经历。完整答案给可打开的依据，语音简述判断与下一步。

## 5. 已核对的源码与真正缺口

本节为静态源码判断，没有导入服务端或检查运行服务。

| 已有能力 | 依据 | 接下来补什么 |
| --- | --- | --- |
| 原消息先保存，候选与写事务核对原话；普通记忆有当前/历史与维护守卫 | [orchestrator.py](../server/dialogue/orchestrator.py#L222)、[manager.py](../server/memory/manager.py#L57)、[store.py](../server/memory/store.py#L271) | 普遍语义支持、完整时间过滤及端到端评分；不是重新建设证据入口 |
| 普通聊天已传入偏好程度、范围和原话日期 | [retriever.py](../server/memory/retriever.py#L16) | 行动助手 [context.py](../server/assistant/context.py#L158)仍只传 value/source/updated_at 等，省略证据状态、scope/as_of；先统一转换，避免把旧/手动 stated 当作已核验原话 |
| 主页事项选择/新建/续接、SQLite 关联选择、最近 12 条事项事件 | [threads.py](../server/interaction/threads.py#L63)、[companion.js](../web/companion.js#L289)、[participation.py](../server/interaction/participation.py#L45) | 针对问题读取更早事件；验证真实跨日体验。PROJECT_DIRECTION.md 的入口旧诊断已修正 |
| 已组合全局笔记选择与事项选择；个别笔记改写有成果版本回执 | [activity.py](../server/interaction/activity.py#L104)、[service.py](../server/assistant/service.py#L238)、[companion.js](../web/companion.js#L47) | 每事项明确的资料/任务/成果引用集合；全局选择组合不是完整关联 |
| 事项候选确认保存文本，来源写 proposal ID | [proposals.py](../server/interaction/proposals.py#L186) | 候选原话在内存，确认后删除；补持久原消息/原话与修订关系，以及回答到依据的可打开引用 |

## 6. 评测实践与推进顺序

[LongMemEval](https://arxiv.org/abs/2410.10813)把长期记忆评测拆为提取、多会话、时间、更新和弃答；论文设计分为 indexing、retrieval、reading。已重新读取[官方 README 固定版本](https://github.com/xiaowu0162/LongMemEval/blob/9e0b455f4ef0e2ab8f2e582289761153549043fc/README.md)。适合借鉴失败分层，而不是立即跑全部题目。

本日也读取了[LongMemEval-V2 官方 README](https://github.com/xiaowu0162/LongMemEval-V2/blob/2cc8c540bdb87fe6761629b585e727e1c4704520/README.md)。它面向多模态 Web/企业轨迹，关注动态状态、工作流程、环境陷阱和前提是否成立；这提示复杂任务的记忆需要保存过程经验。其规模与领域不直接等于本项目，未运行该评测或复现排名。

沿用 A → B → C → D 的投入顺序：

1. A：完成已有真实输出的人工语义评分，取得用户显式开始的电脑外放样本；规划小型完整链路题集，分别记录抽错、误更新、未召回、范围错误和生成误用。
2. 为 B 收敛契约：统一两条记忆上下文转换；给事项增加明确资料/成果版本引用和确认决定的持久来源。
3. 用一件真实、值得反复推敲的作品或方案验证 B：自由讨论 → 提出不同意见 → 修改 → 中断 → 隔天继续。需要工具的步骤另验证确认、真实回执和恢复。
4. 再按失败分布对照摘要展开、混合检索或关系索引。继续使用现有 SQLite，投入以改善找回和接续为依据。

验收看用户重讲背景的次数、找回当前版本和决定依据的准确性、改口后能否接住、打断是否自然、成果是否改善，以及用户是否愿意再次一起讨论。已有测试数量、一次冻结输出重放或厂商分数不能替代这些证据。

新付费评测、生产操作和真实采集未由本轮研究启动。本文是适配建议；第 4 节及第 6 节的后续能力不能当作已实现状态。

用户进一步提出跨时间、会话和项目的管理复杂度，已细化为[记忆组合设计](MEMORY_COMPOSITION_DESIGN.md)：区分来源与应用范围，组合证据、当前视图、过程记录和派生检索；列出第一项上下文改动、候选集合与索引失效缺口及分阶段验收。该文仍为设计，没有启动新 schema 或付费评测。
