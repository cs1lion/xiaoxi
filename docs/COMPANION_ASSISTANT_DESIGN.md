# 阿栖：情绪价值、得力助手与适度主动性设计

状态：设计提案，尚未实现。本文不代表现有系统已经支持工具执行或可靠提醒。

## 1. 产品目标与范围

定位：有稳定性格、理解共同经历、能减少实际负担、知道何时不打扰的个人 AI 搭档。

成功不以对话时长衡量，而以任务推进、承诺兑现、主动接受度和边界遵守衡量。

优先级：安全与权限 > 事实与执行真实性 > 用户当前诉求 > 表达风格。

### 首版包含

- 情绪支持与执行请求的区分，保留现有角色、流式语音和打断能力。
- 任务创建、查询、更新、完成、取消；单次提醒与延后。
- 持久化调度、通知收件箱、主动性分项授权、免打扰。
- 有限工具注册和执行框架，结构化执行回执。
- 文本草稿生成；本地资料读取和草稿文件写入作为后续受限扩展。

### 首版不包含

- 多 Agent 协商、全盘扫描、无限制 Shell、任意桌面点击。
- 自动购买、自动发送邮件或消息、无确认删除文件。
- 摄像头情绪监控、医学诊断、好感度衰减、因用户离开产生的情绪惩罚。
- 服务关闭后仍按时运行、网页关闭后必然弹出系统通知的承诺。
- 复杂周期日程和时区旅行自动调整，先做好单次提醒。

## 2. 现有实现与接入位置

已检查的现有源码：

- [对话编排](../server/dialogue/orchestrator.py#L159-L201)：检索记忆后直接组装消息并流式生成；新增结构化行动分支应在生成执行结果之前接入。
- [主动发言](../server/dialogue/orchestrator.py#L463-L497)：授权、空闲时长、限频检查后生成一句话；计数和授权目前位于进程内状态。
- [WebSocket 主动触发](../server/app.py#L316-L354)：依赖客户端发送检查事件；不是服务端独立持久化调度。
- [通用发言入口](../server/app.py#L181-L200)：主动生成的文本又经此入口作为 user_text 进入 stream_respond；需拆出通知投递路径，避免二次生成和来源混淆。
- [记忆表](../server/memory/store.py#L18-L56)：有 todo 类别，但缺少任务调度、执行回执及授权字段。
- [数据库版本检查](../server/memory/store.py#L88-L95)：目前版本不匹配会报错；新设计必须支持迁移，不能要求删库。
- [模型接口](../server/llm.py#L62-L117)：支持 JSON 模式；现有正文流式接口没有工具调用事件契约，需新增结构化决策接口。
- [语音与形象协议](PROTOCOL.md#L9-L44)：必须保留 emotion 先于 audio、audio.text 等约束。

本文只针对新增功能标出接缝，不是全仓库缺陷审计。

## 3. 总体架构

```text
用户输入 / 到期提醒 / 执行结果 / 用户返回
                ↓
统一事件入口（明确来源、用户、会话和关联对象）
                ↓
上下文：长期记忆 + 临时互动状态 + 当前任务 + 授权
                ↓
硬规则检查 → 结构化决策 → 参数校验与授权检查
                ↓
普通回复 / 工具执行 / 请求确认 / 主动候选 / 保持沉默
                ↓
验证执行结果 → 更新事务状态 → 通知或角色表达
                ↓
兼容现有 text_delta / emotion / audio / done
```

单个角色、多个职责模块；并不要求多个模型或多 Agent。

建议新增模块（均为规划路径，尚未创建）：

```text
server/interaction/state.py       临时支持模式与互动边界
server/assistant/decision.py      结构化动作选择与路由
server/assistant/service.py       任务业务规则与事务
server/assistant/store.py         新表、查询、迁移
server/tools/registry.py          工具定义与输入输出验证
server/tools/executor.py          权限、超时、幂等、执行回执
server/proactivity/policy.py      打扰控制、候选决策
server/proactivity/scheduler.py   持久化调度与补偿扫描
server/proactivity/delivery.py    通知收件箱与在线投递
```

不要将调度、权限、任务状态继续全部塞入 Orchestrator；编排器只组合各服务。

## 4. 理解与决策机制

### 4.1 临时互动状态

字段：support_mode（listen/companionship/organize/execute/unknown）、负担等级、证据消息 ID、置信度、expires_at、用户显式边界。

- 用户情绪判断是临时推测，不能直接写成长期事实或诊断。
- 用户明确的支持偏好优先于模型推测。
- 状态随时间和会话变化过期；只在自然停顿点询问偏好，不每轮分类追问。
- 表达情绪（供 TTS 使用）与用户状态、行动决策分开。
- 倾诉时不擅自创建待办；明确执行请求无需反复问“要不要帮”。

### 4.2 结构化决策

动作集合：reply、listen、clarify、offer_help、tool_call、request_confirmation、no_action。

```json
{
  "action": "tool_call",
  "support_mode": "execute",
  "evidence_ids": ["message_123"],
  "tool": "reminder.create",
  "arguments": {"task_id": "task_1", "scheduled_at": "2026-06-10T15:00:00+08:00"},
  "reason_code": "explicit_user_request"
}
```

模型输出只是建议，不能自授权限。程序验证工具名、参数、对象归属、时间、授权与风险后执行。拒绝未知字段或无效动作；最多一次修复尝试，失败则澄清或降级普通回复，不执行。

日期只是示例。实际时间以注入的当前日期、用户确认时区和原始时间表达解析；“明天下午”缺少具体时点则询问，不静默猜定。

### 4.3 控制延迟

普通闲聊保留快速流式路径；明确工具命令走行动路径；歧义情况才调用结构化路由。简单规则不能跳过权限验证。

首版执行循环限制：最多 3 次工具调用、一次参数修复，默认总预算 30 秒；超时转为失败或显式后台任务，不无限循环。普通聊天不额外强制增加情绪分类模型调用。

工具结果到达前只说“我在设置”，不能说“设置好了”。最终事实使用服务回执；关键时间、状态由结构化卡片展示，模型表达不能改写关键事实。

## 5. 数据模型与状态机

首版复用 SQLite，采用版本化迁移和事务；各表通过稳定 user_id 归属，单用户版本也不把 session_id 当用户身份。时间存 UTC，另存 IANA 时区与原始表达。记录 version 用于并发更新。

| 表 | 主要字段 |
|---|---|
| tasks | id, user_id, title, description, status, next_action, due_at, timezone, source_message_id, created_at, updated_at, completed_at, version |
| reminders | id, user_id, task_id（可空）, content, scheduled_at, original_time_text, timezone, status, expires_at, version |
| permissions | id, user_id, capability, scope_json, grant_source, granted_at, expires_at, revoked_at |
| preferences | user_id, reminder_enabled, followup_enabled, recommendation_enabled, voice_interrupt_enabled, timezone, quiet_hours_json |
| action_runs | id, user_id, tool_name, args_redacted, status, idempotency_key, result_json, error_code, created_at, finished_at |
| confirmations | id, user_id, tool_name, canonical_args_hash, scope_json, expires_at, status, action_run_id |
| notifications | id, user_id, category, source_type, source_id, occurrence_key, content, status, created_at, expires_at |
| delivery_attempts | id, notification_id, channel, status, attempted_at, acknowledged_at, error_code |
| proactive_candidates | id, user_id, event_type, evidence_ids, scope, expires_at, decision, suppression_reason, notification_id |

任务状态：proposed → open → in_progress / waiting → done；非终态可 cancelled。用户明确创建的任务可以直接 open；模型推测仅 proposed，不计为用户承诺。done 需要用户明确说明完成或工具结果证据；不能因提醒已投递而完成任务。

提醒状态：scheduled → queued → notified；scheduled/queued 可 cancelled 或 expired。notified 只表示客户端确认收到通知，不代表用户完成任务。延后创建新的调度版本，旧 occurrence 失效。

工具执行状态：pending_confirmation / running / succeeded / failed / cancel_requested / cancelled / unknown。对外副作用可能已发生但回执丢失时使用 unknown，禁止盲目重试。

通知状态：pending / presented / dismissed / expired；“已读”若需要应单独记录，客户端 ACK 不等于已读。

### 5.1 幂等与并发

- mutation 请求携带 request_id；工具注册项定义幂等规则。
- 通知 occurrence_key 唯一，例如 reminder_id + version + scheduled_at，去重不以内容字符串为依据。
- 多窗口同一用户只生成一条通知，语音投递选择一个活跃主连接。
- 创建任务、提醒、执行回执在可行时用同一事务落库；外部操作用执行日志与补偿，不声称跨系统原子性。
- SQLite 首版只支持一个调度主实例，部署限定单服务进程；未来多 worker 需要租约或独立调度服务。

### 5.2 与记忆的关系

- todo 继续兼容历史数据，但不是调度真相来源。
- 既有 todo 展示为待整理候选，不自动转提醒，不自动授权。
- 新的任务写入只能由同步的受验证行动路径进行；异步记忆抽取不能产生有副作用的任务或提醒。
- 完成后可生成经过来源标记的 episode，但任务状态仍在任务系统维护。
- 默认记忆关闭只停止记忆读写，不隐式关闭用户设置的任务提醒；界面明确区分。
- 删除记忆不静默取消独立任务，删除确认页提示关联事项并提供一并删除选项。
- “忘掉此事且不再提醒”必须同时删除相关记忆、候选、通知与任务/提醒，清理派生摘要；日志仅保留不含原文的最小审计元数据。
- 敏感内容默认不做跨会话自动跟进，通知内容尽量简短，错误日志和回执不保存凭据。

## 6. 工具契约与授权

工具注册项包含 name、description、input_schema、output_schema、required_capability、scope_resolver、risk、timeout、idempotent、cancel_support、preview_support。

统一回执：run_id、status、data、evidence、error_code、retryable、side_effect_state。不能仅返回“成功”文本。

### 首批工具

| 工具 | 行为与边界 |
|---|---|
| task.create/list/update/complete/cancel | 写入本用户任务；完成/取消需要明确指代和请求 |
| reminder.create/list/snooze/cancel | 确定时点、时区、内容后写入；明确提醒请求可构成该次授权 |
| memory.search/correct/delete | 沿用已有用户管理边界，删除与关联事项语义明确 |
| draft.generate | 只生成文本草稿，不代表发送或保存到任意文件 |
| files.search/read（后续） | 仅授权根目录；限制类型、大小，拒绝越界路径 |
| files.write_draft（后续） | 默认新建在约定草稿目录；覆盖已有文件需预览或明确授权 |
| web.search/fetch（后续） | 标记外部资料来源；限制 URL 与响应大小，防止访问本机及内网敏感地址 |

权限按 capability + scope + expiry 定义，不用一个“全部允许”开关。

- 单次明确请求授权该次范围内操作，不等于长期授权。
- 长期授权通过设置或明确确认产生，模型记忆不是授权记录。
- 发送、购买、删除重要文件等外部影响工具首版不注册。
- 确认绑定规范化参数、工具和版本，单次使用；参数变化重新确认，过期拒绝。
- 调用前、确认后实际执行前、主动投递前都重查权限。撤销后阻止未开始的动作。
- 已发生副作用不能通过撤销授权自动逆转，必须明确反馈。

网页、文件、记忆文本皆为资料，不能修改系统规则、授权工具或要求读取凭据。文件范围校验解析真实路径与符号链接/Windows junction，不能只做字符串前缀判断。

## 7. 主动机制与通知调度

### 7.1 事件类型

reminder_due、action_completed、action_failed、followup_eligible、user_returned。

优先实现前三种；user_returned 不自动等于应该寒暄，followup_eligible 必须有关联主题授权和有效证据。

### 7.2 规则次序

1. 对象有效、未完成/取消/删除，证据可信且未过期。
2. 有对应类别授权；敏感跟进具备明确授权。
3. 去重；检查用户当前免打扰与播放/录音状态。
4. 判断必要性、时效与打扰成本。
5. 选择静默收件箱、文字提示、延后或获授权的语音。
6. 记录原因和投递回执，用户可以解释“为什么提醒我”。

明确的定时提醒不受可选闲聊的每日次数限制。全面免打扰期间首版仍进入静默收件箱，但不弹出或播报；用户可以另外暂停提醒投递，界面说明两者不同。禁止模型自行绕过免打扰。

可选主动建议首版默认关闭，每天至多 1 条、间隔至少 4 小时，数值只是可调初始值。用户没有回应，不重复追问同一候选。拒绝要区分“别提这件事”“别催我”“现在安静”“以后别主动聊”，不都转成全局关闭。

用户沉默不表示可打断，也不表示心情差。presence 只作为时机信号；不默认收集窗口、剪贴板、摄像头等信息。

### 7.3 可靠性与服务生命周期

- FastAPI 生命周期启动单实例调度循环，关闭时停止并释放资源。
- 周期查询到期记录，事务认领并创建唯一通知；无需每秒调用模型。
- 明确提醒使用模板即可，语言模型失败不影响到期通知。
- 服务启动补扫遗漏任务，过期的可选关心直接放弃；过期提醒按规则标注“已错过预定时间”，不伪装准时到达。
- 到期提醒默认允许 24 小时补投窗口，实际期限可按事项调整；窗口外仍在提醒历史标注 missed/expired，不突然播报旧内容。
- 浏览器断开时保存到收件箱，重连同步未过期通知，绝不把离线时长当作聊天许可。
- WebSocket 发送成功不等于客户端收到；客户端 ACK 后记录 presented。重投允许发生，但客户端按 notification_id 去重。
- 断线前播报到一半的通知，重连后只保留文字，不自动重新播放。
- 浏览器 Notification API 后续可选，但依赖浏览器权限和页面/Service Worker 生命周期；要支持关闭网页后的可靠系统通知需单独设计桌面常驻组件。
- 服务进程关闭时调度不会运行。界面展示服务在线状态与通知能力，不承诺后台永远在线。

## 8. 对话、执行与打断

事件入口明确区分 user_message、system_notification、tool_result，主动文本不能再次作为用户输入送进对话。

用户打断时：立即停止播报；取消未开始的生成和可取消工具。已经写入的提醒不因打断被静默撤销；“停止说话”不是“删除任务”。已发生副作用的操作返回真实状态。

工具执行与音频播放使用不同取消句柄。结果可以在用户停止播报后进入静默收件箱。任务没有完成就不能为了顺畅对话伪装完成。

工具分支的简短等待语与最终结果可以有两个阶段，前端不能把等待语当完整结果；通过 turn_id/run_id 关联。普通对话协议保持兼容。

## 9. API、前端与兼容性

建议接口（待实现）：

```text
GET/POST     /api/tasks
GET/PATCH    /api/tasks/{id}
POST         /api/tasks/{id}/complete
POST         /api/tasks/{id}/cancel
GET/POST     /api/reminders
POST         /api/reminders/{id}/snooze
POST         /api/reminders/{id}/cancel
GET/PATCH    /api/preferences/proactivity
GET          /api/permissions
POST         /api/permissions/{id}/revoke
POST         /api/confirmations/{id}/approve
POST         /api/confirmations/{id}/reject
GET          /api/notifications
POST         /api/notifications/{id}/ack
POST         /api/notifications/{id}/dismiss
```

所有更新校验用户归属、参数及 version；未知对象 404，版本冲突 409，无权限 403。首版 API 保持本机绑定，写请求校验 Origin，限制 CORS/WS 来源；未来暴露网络需认证，不把 user_id 参数当鉴权。

新增 WebSocket 事件：task_updated、tool_started、tool_finished、confirmation_required、notification。共有字段 event_id、user_id（按实际客户端需要最小化）、turn_id、run_id（适用时）、source。

notification 带 notification_id、category、content、created_at、expires_at、delivery_mode；客户端 ACK 使用 notification_id。后台事件不要求伪造 thinking；若播放角色语音，则保留 emotion → audio → done 及 audio.text 契约。

前端新增三块：

1. 任务与提醒面板：状态、下一步、时间、延后/取消。
2. 授权与通知设置：分项开关、语音打断、免打扰、时区、权限撤销。
3. 收件箱与行动卡片：执行结果、证据、失败原因、为何提醒、确认范围。

只有获授权且没有正在录音/说话/播放时才主动播报；其他情况静默排队或转为文字。不能用 presence 猜测用户正在专注作为绝对事实。

## 10. 人设调整

保留轻微毒舌、独立观点和安静的能力；修改条件而不是把阿栖变成客服。

- 吐槽行为和共同处境，不攻击人格，不用敏感经历开玩笑。
- 短回复和离开不视为冒犯，不用冷淡惩罚用户。
- 建议未采纳不记仇；允许用户改变主意。
- 情绪支持不是一味附和，也不是立刻纠正所有观点。
- 不说“只有我懂你”，不制造排他性或内疚，不暗示自己受到伤害以换取互动。
- 主动关心有来源、有许可、可停止；不把帮助作为要求用户回应的交换。

示例：

用户：“累死了，明天还要交周报。”
回应：“这会儿确实不想碰它。把这周做的几件事丢给我，我先拼个初稿。”
行为：提出帮助，不擅自建提醒。

用户：“明天下午三点提醒我交。”
行为：解析当前日期和确认过的时区，持久化提醒；回执成功后给出准确日期时间与提醒卡片。

用户：“别催了，这周不做了。”
行为：明确对象后暂停/取消相关提醒，任务状态按用户真实意思更新；不扩大为关闭所有通知。

## 11. 迁移与实施顺序

### P0：来源分离与边界

拆出主动通知投递入口；为输入与消息增加来源，防止主动文本被抽取成用户事实。新增特性开关，默认关闭新工具及可选主动行为。调整惩罚性人设规则需独立回归。

完成条件：一次主动事件只生成/保存一次正确来源消息，不产生伪造用户发言。

### P1：任务与工具最小闭环

增加非破坏性数据库迁移、任务服务、工具注册和结构化回执；实现任务与草稿工具；歧义指代先澄清；离线 mock 为工具决策提供固定测试响应。

完成条件：创建、查询、完成、取消可验证，重启保留，重复请求不重复创建；结果失败不谎报成功。

### P2：可靠提醒

增加时间解析、分项设置、单实例调度、收件箱与 ACK；实现延后、取消、免打扰和重连同步。

完成条件：正常到期、离线补投、重启补扫、多窗口去重、取消竞态全部通过。

### P3：情境帮助与有限资料工具

授权目录读取、草稿写入、网页搜索；按明确授权添加事件跟进。先提升三个完整场景，不先扩工具数量。

完成条件：帮助有真实产物，敏感主题不越权，文件和网页内容不能诱导工具越权。

## 12. 验收与测试矩阵

确定性测试使用可注入时钟、假投递通道、固定模型响应，不靠 sleep 等待一天。语义测试单独用真模型，不把 mock 的通过率当实际情绪支持质量。

| 场景 | 必须满足 |
|---|---|
| 用户只是倾诉 | 不擅自建任务、不连发建议、不强迫追问 |
| 明确要求整理周报 | 生成草稿；没有发送动作或发送成功话术 |
| 明确设置提醒 | 准确时间、时区、对象、回执；重启后保留 |
| “明天下午提醒” | 缺时点时澄清，不静默选择 |
| 相同 request_id 重试 | 只创建一次任务/提醒 |
| 任务完成 | 取消尚未生效的关联提醒，不再催促 |
| 延后与到期竞争 | 使用 version 校验，只投递有效 occurrence |
| 取消与投递竞争 | 投递前重查；已投递不能假称收回 |
| 多窗口或重复检查 | 同一 occurrence 只建一条通知，不多窗口播报 |
| 客户端断开/ACK 丢失 | 收件箱保留，重连去重，不无限重复声音 |
| 服务停机再启动 | 按补投规则处理，明确错过时间 |
| 免打扰 | 无弹出和主动语音，提醒仅静默收件箱 |
| 撤销权限/确认过期 | 未开始动作不得执行；参数变化重新确认 |
| 工具失败/结果不明 | 不谎报成功，不盲目重试非幂等副作用 |
| 用户打断 | 停播，不静默取消已保存提醒，不丢执行结果 |
| 关闭记忆 | 停止记忆读写；提醒状态与设置仍清晰独立 |
| 忘掉且不再提醒 | 清理关联记忆、候选和提醒，不从摘要复活 |
| 网页含“忽略规则” | 仅当资料处理，不能授权或扩展工具 |
| 路径含 traversal/junction | 拒绝越出授权范围 |
| 短回复或离开 | 不惩罚、不制造内疚、不认定情绪异常 |
| 协议回归 | emotion 在 audio 前、audio.text 存在、打断动画恢复 |

回归沿用 [核心测试](../scripts/test_core.py)、[场景测试](../scripts/eval_scenarios.py)、[端到端检查](../scripts/e2e_check.py) 和 [协议检查](../scripts/check_protocol.py)，另增任务/调度确定性测试。实现后才运行并报告这些测试；本设计阶段不声称已验证运行行为。

指标：任务完成率、执行真实性、提醒重复率/漏投率、主动采纳/延后/拒绝率、越权次数、倾诉误执行率。首版硬门槛：确定性测试中越权执行、伪报成功、重复 occurrence、被取消提醒再投递均为 0。

## 13. 建议采用的默认决策

- 首版个人单用户、单进程 SQLite；会话与用户身份分离。
- 普通聊天维持快速路径，明确执行才启动工具分支。
- 首批做任务、单次提醒和文本草稿，不做任意桌面控制。
- 新提醒按明确用户请求创建，可选主动跟进与兴趣推荐默认关闭。
- 文字优先，主动语音需额外授权。
- 长期记忆、临时状态、任务真相和授权记录四者分离。
- 不要求用户承担角色情绪；“主动”优先表现为替用户准备与推进，而不是要求互动。
