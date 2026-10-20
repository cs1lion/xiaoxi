# Agent Runtime：方向、已实现机制与边界

本文记录本次实现，不将设计提案中的全部能力视为已完成。

## 参考来源与选择

- [OpenAI Agents SDK：Running agents](https://openai.github.io/openai-agents-python/running_agents/)：模型输出工具调用后执行、回填结果再运行；final结束；max_turns防止无限循环。本项目采用同类 bounded decide → act → observe 循环，保留现有 OpenAI 兼容接口，不引入SDK依赖。
- [LangGraph：Persistence](https://docs.langchain.com/oss/python/langgraph/persistence)：将状态持久化以支持恢复、人工干预。本项目实现 SQLite checkpoint 与参数绑定确认，尚不是完整图运行时，不自动重放可能已产生副作用的中断动作。
- [Letta 当前 SDK Memory](https://docs.letta.com/agent-sdk/memory/index.md)、[MemGPT](https://research.memgpt.ai/)：分层管理有限上下文，重要信息与按需检索分开。本项目复用已有长期记忆，加入有限预算 ContextBuilder；不是完整 Letta/MemFS 实现。

在线搜索插件曾报401；来源通过官方直链抓取及独立研究读取，不依赖无法验证的搜索摘要。

## 已落地结构

- [loop.py](../server/assistant/loop.py)：每轮最多4次决策、3次工具调用、30秒预算。工具白名单、严格类型与参数验证；模型不自授权限。每次调用结果形成receipt，再回填模型。异常、超时、取消和步数耗尽都有终态。
- [runtime.py](../server/assistant/runtime.py)：持久化public checkpoint、事件和单次确认；不记录隐藏推理。确认绑定完整action hash、run_id和时限，重复/过期确认拒绝。服务重新构造时running标记interrupted，不盲目重放写操作。
- [context.py](../server/assistant/context.py)：最长12000字符上下文；分开用户明确陈述、推测与不确定记忆，临时支持模式15分钟TTL；任务不等于授权。记忆关闭后不访问检索器。常见凭据脱敏，但不是任意秘密识别保证。
- [store.py](../server/assistant/store.py)：独立任务/提醒/偏好/通知数据库，避免破坏现有记忆库；事务、请求幂等、版本校验、到期扫描、24小时过期窗口、延后去重、完成/取消任务清理关联提醒。
- [service.py](../server/assistant/service.py)：工具注册和JSON决策模型适配；普通聊天保持原路径，明显任务/提醒请求进入Agent Loop。所有模型提出的写操作首版需要用户逐次确认，保守于“明确请求可直接执行”的最终设计。
- [api.py](../server/assistant/api.py)：任务、提醒、偏好、收件箱、run与确认接口；写接口Origin检查；服务生命周期每2秒扫描到期提醒，不调用模型。
- [assistant.js](../web/assistant.js)：任务/提醒面板，精确参数确认、回执展示、收件箱每5秒同步和ACK。首版没有主动语音播报，不假装网页关闭后可通知。

## 写动作 outbox（2026-10-20）

`runtime.py` 的 `agent_outbox` 把“操作意图、真实回执、checkpoint”放在同一个
SQLite 事务里，`operation_id = run_id + action_seq`：

- `begin_operation()` 在副作用前写 `started`；`finish_operation()` 在工具返回后
  原子写入 `succeeded/failed`、receipt、run checkpoint 和 `tool_finished`。
- 启动时 `recover_interrupted()` 把仍然 `started` 的操作标为 `unknown`，并追加
  `operation_unknown` 事件；**不猜测成功，也不自动重放**。`resume_plan()` 遇到
  unknown 直接拒绝，必须人工核对。
- `AssistantService._flush_outbox()` 把已提交的回执按 `run_id:observation_index`
  幂等投影到共同事项；服务启动时也会补投，事项已删除/冲突则记 dead letter。
- `task.create/update` 与 `reminder.create/cancel/snooze` 通过服务端注入的
  `request_id=operation_id` 使用现有幂等表；模型看不到这个参数。
- `world.dispatch` 通过 `CommissionStore.create(..., request_id=operation_id)`
  在同一台账事务里去重；重放返回原单，且在并发满之前就短路，不会重复 drive。

回归：`scripts/test_operation_outbox.py` 覆盖正常投影、二次投影、启动补投、
handler 成功前崩溃、unknown 阻断 resume，以及三类 store mutation 的重放。

## 数据与记忆原则

长期记忆是真实来源明确的信息；任务系统保存执行真相；临时支持模式只是当前互动偏好；授权只由真实确认产生。四者不互相替代。

上下文允许“没找到相关记忆”，但不将其解释成“事情没有发生”。状态恢复只恢复公开决策和工具结果，不需要保存模型的思考过程。

现有记忆层仍有待逐步改进的主体/key冲突及广义删除语义。本次优先修正检索相关度、推测过期及注入预算；不以有风险的自动迁移改变用户既有事实。

## 运行与验证

沿用原项目启动方式。任务管理无需在线模型；自然语言决策需配置现有LLM。没有API key时只给离线澄清，不编造操作。侧栏任务面板可直接管理任务及提醒。

```powershell
.\.venv\Scripts\python.exe scripts/test_agent_loop.py
.\.venv\Scripts\python.exe scripts/test_assistant_store.py
.\.venv\Scripts\python.exe scripts/test_assistant_context.py
.\.venv\Scripts\python.exe scripts/test_assistant_api.py
.\.venv\Scripts\python.exe scripts/check_protocol.py
```

新助手测试使用独立临时数据库及fake模型。核心测试现已在导入单例前强制设置内存数据库，配置也新增 DB_PATH 支持；已实际运行38项并用只读SQLite逻辑指纹验证真实库不变。

重要测试事故说明：修复前错误地以为设置 DB_PATH 已实现隔离，但旧配置忽略此变量；此前运行的核心测试含 delete_all，可能已清空默认记忆库的 memories 表。没有确认可用备份，不声称已恢复原记忆。历史消息与记忆表不是同一数据，未擅自用历史消息重建用户记忆。

最近验证：Agent Loop 8项、任务存储15项（一次Windows文件占用清理错误后独立重跑通过）、上下文12项、HTTP 3项、WebSocket 2项、记忆检索11项、原核心38项；语音协议检查与隔离Playwright侧栏/确认/回执/收件箱测试通过。尚未进行真实模型完整语义验收。

## 当前限制与下一步

- 单用户、本机、单进程调度，尚未实现网络用户认证和多worker租约。
- 收件箱文字投递，不会语音打断；网页关闭后只有服务器保存通知，服务器关闭时不调度。
- 模型可生成文本草稿，尚不开放任意文件写入、网页搜索、Shell、邮件发送或桌面控制。
- 确认等待可跨进程恢复；生成中断不自动恢复，避免重复写入。暂停后的再次执行仍需原参数确认。
- JSON严格解析失败当前安全终止，尚无自动格式修复；基于关键词的快速路由不是完备语义意图分类。
- 行动分支与主页面确认结果已接入可取消TTS；clarify可在同session续答。详见[有声行动与澄清续答](VOICE_ACTION_CONTINUATION.md)。后台通知收件箱仍为文字投递。
- 任务工具回执结构化展示；模型最后的文本仍需真模型语义测试检查，不将fake通过视为真实模型不谎报的保证。
- 全范围忘记（包括历史会话及任务）需要单独设计和迁移，不声称现有删除记忆接口已满足。

后续优先：真实模型端到端验收 → 来源与删除语义完整化 → 受限资料工具 → 获授权事件跟进。避免先扩展多Agent或无限工具能力。
