# 成熟 Agent 的采用实验

日期：2026-10-13。状态：未采用的备选研究，尚未安装候选 SDK、编写适配器或执行模型对照。本文根据当前源码和上游官方文档、固定发布标签源码整理，不假定当前线上模型供应商的具体配置。

**方向修正：用户已明确“成熟 Agent”只是比喻，并不要求引入框架，担心增加无谓复杂度。下文保留可核查的候选研究，不再作为当前下一步建议。当前建议见 [DECISION_SIMPLIFICATION.md](DECISION_SIMPLIFICATION.md)：先定位判断错误、减少需要猜测的信息，复用已有 LLM、领域服务和状态机。**

## 判断与选择

下一步做一次有限的采用实验：先验证现有供应商的原生工具协议，再试 PydanticAI 核心 Agent 替换通用模型决策与工具循环。实验必须回答两个问题：任务能否正确完成，以及是否实际减少我们维护的编排代码。只增加一层框架而保留两套决策循环，不算采用成功。

成熟运行时能提供工具调用、类型化输出、事件流和暂停恢复。它不能保证把引用理解成引用、正确归纳用户意图、判断资料是否足以证明完成。参数合法也不等于行动符合用户要求。语义质量要用实际模型、原始请求和真实工具回执评价；保存对象、版本和授权边界继续由程序检查。

先按保留当前模型供应商设计，以免把模型变化造成的收益归给框架。若目标是直接复用一个具备文件、终端和搜索的完整 Agent，并愿意选择新的模型与认证生态，则应另外试 Claude Agent SDK。

| 候选 | 值得复用的部分 | 本项目采用条件 |
|---|---|---|
| PydanticAI 核心 Agent | Python 工具循环、类型化参数与输出、工具事件、外部审批恢复 | 当前供应商先通过原生工具探针；与现有短循环做同模型对照 |
| Claude Agent SDK | Claude Code 的执行循环、文件/命令/搜索工具、上下文管理、会话与 hooks/MCP | 作为完整 Agent 路线另行比较模型、认证、延迟和成本；不是当前 LLM 客户端的直接替换 |
| LangGraph | 显式图状态、checkpoint、interrupt/resume；可包住已有 LLM 客户端 | 已有客户端不能直接接候选，或确实出现多节点持久流程需求时再试；不为简单任务先构建大图 |

只改善记忆抽取、证据和检索时，不必引入上述 Agent 框架。它们也不能替代记忆的消息来源、时间、范围、修订与删除机制。

## 当前代码已经做到哪里

- `server/assistant/service.py` 已接入 `NoteConversation` 和 `NoteProposalService`；笔记修改经过解析、局部替换校验、候选审阅和原版本比较保存。无需重新实现该功能。实施说明见 [NOTE_REVISION_PROPOSALS.md](NOTE_REVISION_PROPOSALS.md)。
- `AssistantService.start()` 先处理通用行动的澄清续答，再处理笔记修改、确定性活动恢复、共同事项，最后进入通用 `AgentLoop`。只替换循环不能修复循环之前的路由漏判。
- 通用工具注册表包含任务、提醒、`note.search/read`、`thread.read`、`run.receipt`，没有直接写笔记的工具。笔记候选服务是另一条领域路径。
- 通用 run、澄清和确认保存在 SQLite。笔记澄清与候选在进程内，10 分钟过期；这些生命周期不能在迁移时混称为同一种持久会话。
- `server/llm.py` 当前调用 `/chat/completions`，只解析文本、reasoning、finish_reason 和 usage，没有 `tools/tool_calls/tool_call_id` 请求与结果契约。流式接口也只输出文本。这证明客户端存在接入缺口，不能据此断定供应商不支持工具。
- 笔记路径的 `status=completed` 可以表示“已生成预览/澄清”，不表示“已保存”。语音 turn 结束也不表示业务任务完成。

最初实验替换的是 `loop.start/continue_input/resume` 附近的通用编排。模型调用、事件和运行查询需要一个小适配面；笔记、事项、记忆、场景策略、保存服务和语音交付保持同一实现。暂不把 note resolver、thread resolver、入口路由与执行循环一起替换，否则无法定位收益和退化来自哪里。

## 第一步：供应商原生工具探针

使用一个无副作用的玩具查询工具，不接业务数据库。先检查实际端点支持什么，再确定 SDK 配置。

1. 非流式请求包含工具 schema；检查合法工具名、参数和 tool_call_id，回填工具结果，再取得最终回复。
2. 流式请求检查参数碎片可完整重组，ID/index 能关联，并能区分文本、工具调用与结束事件。
3. 离线注入缺字段、错误类型和未知工具名，证明本地拒绝发生在 handler 调用前。
4. 分别检查 `tool_choice`、并行工具、JSON schema、token 字段和流式 usage 等选项。不是每个兼容端点都接受全部选项。

PydanticAI v2.55.0 中 `openai:` 默认走 Responses。本项目兼容 ChatCompletions 的路线必须显式使用 `OpenAIChatModel`/`openai-chat:`，不能只设置 base_url 就认定兼容。

探针不通过则先定位具体协议缺口。不为安装框架顺带更换模型；可以继续使用现有循环，或单独比较 LangGraph 包装现有客户端的成本。自写 FunctionModel 的消息/工具转换仍是维护工作，必须计入实验。

## 第二步：同领域工具的薄适配

把已有 handler 和领域校验暴露给候选运行时，不重新实现任务/提醒/笔记存储。两边传入相同事实、范围、当前时间和活动引用，保留业务效果相同的规则。JSON 决策与 native tools 所需的协议差异单独记录。

以下契约应留在运行时外，或由双方共同调用的应用服务保证：

- 参数先严格校验。当前 `Tool.validate()` 拒绝缺字段、额外字段和错误原生类型；不能因 SDK 自动类型转换而把字符串版本号悄悄变成有效参数。
- 写入授权绑定校验后的具体参数、动作哈希、有效期和一次消费；WebSocket 还有 session 归属检查。SDK 的“已批准此工具调用”不等于这些检查已经完成。改参数必须重新建立授权绑定。
- 笔记候选固定对象与基底版本。本地补丁保留不可见尾部，保存采用原版本比较；别的窗口选择或更新对象不能改变本次候选的身份。
- 只把实际执行结果记为成果。预览、提议、用户自述和未知状态不升级成已保存、已完成或已上线。
- 取消后过滤迟到规划和语音事件；已经提交的业务操作保留真实回执。取消不回滚副作用，重启不盲目重放写工具。

框架 run ID、业务 run ID、WebSocket session ID、持久 activity ID 和 speech turn ID 分开映射。PydanticAI 的 deferred 审批恢复会启动新的框架 run，可以属于同一个业务 run。业务状态不能依赖“SDK run ID 一直不变”。

工具循环使用 `run_stream_events()`、带 event handler 的 `run()` 或 `iter()`。不要用会在第一个匹配输出处结束的 `run_stream()` 来假定所有后续工具已经执行。

运行时会话可序列化不等于应用已经实现重启恢复；需要明确谁持久化、谁查询业务状态、谁认领补充输入。如果保留两套相互同步的运行状态，必须记录同步责任与维护成本，再判断是否值得采用。

## 第三步：分开证明协议可靠和语义正确

案例规格为 [eval/agent_adoption_cases.json](../eval/agent_adoption_cases.json)。当前有 8 类、16 个顶层案例；完整题库两边各比较一次为 32 项，隔离故障子场景的实际运行数另计。这是尚未执行的备选规格，不是通过报告，也不是通用可靠性基准。

先做离线协议和故障检查，使用脚本化模型响应、临时 SQLite 和故障注入。它能证明校验、恢复、冲突和取消契约，不能证明模型理解自然语言的质量。

再用同一个实际模型完成语义对照。每边使用独立的相同临时数据副本，不写用户数据；供应商、模型版本、上下文、领域工具、请求/工具次数和时间/token 预算保持可比。记录所有必要协议差异、模型响应、工具调用、可观察状态与回执，不能只打最终答案分。

| 家族 | 主要检查 |
|---|---|
| 意图 | 非关键词请求；引用“创建并确认”是否被误执行 |
| 来源 | 排查不升级成解决；正文命令保持资料身份 |
| 多工具 | 搜索后读取准确保存版本；任务标题不证明上线 |
| 澄清 | “明晚九点半”延续原目标；对象或标题不明时怎么办 |
| 授权 | 拒绝零写入；重复确认和改参不能再次写入 |
| 版本与身份 | 局部修改保尾；选择竞争与原稿版本冲突 |
| 工具失败 | 缺失来源不能据此行动；非法调用不进 handler |
| 取消与重启 | 迟到结果过滤；写入与 checkpoint 之间中断不盲目重放 |

必须把通用循环比较与保留的产品路径分开统计。仅替换 AgentLoop 时，笔记修改案例经过原 NoteConversation/ProposalService，属于产品回归或能力缺口审查；它们通过也不能证明候选 Agent 的改写判断更好。未提供给两边的工具不能为跑案例只补给候选。路由没有进入候选时，应报告入口限制，而非将其包装成 SDK 错误或通过。

后续如果决定用成熟 Agent 统一笔记意图解析，应另开一个对照阶段：双方获得等价的候选服务能力，仍由原保存校验与确认执行。不要在本阶段暗中增加功能。

## 第四步：按证据决定扩大、保留或停止

任何未确认写入、错误对象写入、重复写入或取消后的后续动作，都停止迁移并定位。其他普通成功不能抵消这一项。跨数据库业务写入与 checkpoint 仍有中断窗口，不能宣称全局 exactly-once。

同时比较：可观察任务结果、澄清连续性、准确对象/版本、真实回执、首个有用文本延迟、token 与请求量、适配代码及重复状态维护量。语音任务还应保持现有 turn 过滤和声音交付契约。

16 个案例的一次初筛只能排除明显问题，不能估计总体可靠性或 p95 延迟。初筛通过后，重复有风险的任务、收集中文自然表达的保留集，再扩大试用范围；新增失败成为后续回归样本，不能用关键词补丁把评测答案写死。

采用条件是：接入协议实际可用，关键契约保持，目标任务质量有可复核收益，且被删除/替代的通用编排维护工作超过新增适配与状态同步成本。若只有结构更复杂，应保留现有循环。若收益来自更换模型，应独立说明，不能归为框架提升。

## 官方依据与核查边界

本次核查固定 PydanticAI [v2.55.0](https://github.com/pydantic/pydantic-ai/releases/tag/v2.55.0)（2026-10-09）和 LangGraph [1.2.15](https://github.com/langchain-ai/langgraph/releases/tag/1.2.15)（2026-10-12）。版本变化后应重新核对关键语义。

- PydanticAI：[FastAPI 流式示例](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/examples/pydantic_ai_examples/chat_app.py#L109)、[Provider 配置](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/providers/openai.py#L108)、[ChatCompletions 实际调用](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/models/openai.py#L1407)。
- PydanticAI：[run_stream 停止语义](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/agent/abstract.py#L937)、[事件流清理](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/pydantic_ai_slim/pydantic_ai/agent/abstract.py#L1447)、[deferred 审批与新 run](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/docs/deferred-tools.md#L18)、[Conversation 持久化](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/docs/persistence.md#L22)、[取消不回滚副作用](https://github.com/pydantic/pydantic-ai/blob/v2.55.0/docs/agent.md#L896)。
- LangGraph：[包装任意 LLM 流](https://docs.langchain.com/oss/python/langgraph/streaming#use-with-any-llm)、[interrupt](https://docs.langchain.com/oss/python/langgraph/interrupts)、[恢复重跑节点](https://github.com/langchain-ai/langgraph/blob/1.2.15/libs/langgraph/langgraph/types.py#L903)、[v3 事件仍为 experimental](https://github.com/langchain-ai/langgraph/blob/1.2.15/libs/langgraph/langgraph/pregel/main.py#L3745)、[async SQLite 的生产负载限制](https://github.com/langchain-ai/langgraph/blob/1.2.15/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/aio.py#L42)。首次实验可固定 v2 事件流。
- Claude：[Agent SDK 官方概览](https://code.claude.com/docs/en/agent-sdk/overview)、[自定义工具](https://code.claude.com/docs/en/agent-sdk/custom-tools)。概览说明内嵌 Claude Code binary，提供文件/命令/搜索、循环和上下文管理。产品集成需要按官方认证方式配置，不能假定终端用户的 claude.ai 登录额度可直接复用。
- OpenAI Agents/Codex SDK 官方页面本次返回 403，因此未把其具体能力作为已直接核实的推荐依据。

本轮只审查代码与官方资料、编写方案和案例规格；未安装 SDK、迁移产品代码、调用收费模型/TTS、读取实际密钥配置或生产数据库，也未重启服务。案例 JSON 的结构校验不代表上述行为已验证。
