# 场景上下文与偏好边界：实施契约

## 数据域与协同

角色规则、原创表达示例、真实经历、沟通偏好、任务/授权仍为独立数据域。新增 interaction 模块仅承载偏好及其证据，不替代任务权限与原始记忆库。

- [PreferenceStore](../server/interaction/preferences.py)：证据、结构化偏好、范围、状态和修订关系；SQLite独立持久化。
- [ContextBroker](../server/interaction/broker.py)：每轮先过滤状态与范围，再选最具体偏好；把结果转成有限行为通道。不注入原始私人证据，不缓存偏好值。
- [ResponseComposer](../server/dialogue/composer.py)：接受协调层提供的默认行为，当前明确请求再覆盖；示例仍仅为表达素材。

应用将同一个 broker 绑定给普通对话与助手服务。旧版从自由文本事实自动解析长期偏好的路径不再用于普通对话；避免来源只有stated标签就升格为稳定沟通规则。已有composer的兼容API仍保留供独立测试，应用使用结构化偏好入口。

## 偏好范围

当前支持四个维度：humor(off/light)、detail(short/detailed)、support_style(listen/help)、question_style(minimal/normal)。scope支持activity、session_id、topic的精确匹配。

活动范围为chat/work/writing/technical，互不隐式包含。工作不自动包含技术，避免父场景范围未经定义就外溢。activity可由受信任调用方显式提供；否则保守关键词判定，topic无可靠来源时不猜。

规则：当前明确要求 > 匹配范围内最具体的active偏好 > 角色默认。不同维度协同；同维度同等具体但值冲突时不使用。不因更新时间较新就自动覆盖冲突。

## 来源、修订和删除

写入偏好必须引用存在且属于用户的证据。显式用户/手动设置才可active，inferred只能candidate。source不是模型可自动升级的置信度。

- create_claim：普通新增；同范围同维度冲突显式暴露。
- revise_claim：用户明确修订，保留范围及来源，旧记录superseded。
- delete_evidence：清除原文及所有依赖claim，下一轮立即停止其影响。
- delete_claim：停止指定偏好；不假称原证据也被删除。

这不是全项目彻底遗忘：现有聊天日志、Agent Runtime旧checkpoint、备份等有独立生命周期，当前接口只保证偏好库及每轮broker读取。已经生成的历史回复不会自动改写。后续应增加统一删除事件与各域订阅，不能把本模块删除夸大成全部副本清除。

## 接口

```text
GET  /api/interaction/preferences
POST /api/interaction/evidence                  {text}
POST /api/interaction/preferences               {dimension,value,evidence_ids,scope?,valid_until?}
POST /api/interaction/preferences/{id}/revise    {value,evidence_ids}
POST /api/interaction/preferences/{id}/delete    {}
POST /api/interaction/evidence/{id}/delete       {}
```

接口仅用于本机单用户显式管理，不注册给模型作自动画像工具。证据来源由API固定为explicit_user，不接受任意主体或来源字段。侧栏现已提供独立「相处偏好」面板，可新增类型/值/活动范围/到期时间与用户说明，按需查看原始证据，修订或删除。修订保留原scope（含API创建的session/topic）及到期时间；候选不提供一键升格。来源读取 GET /api/interaction/evidence/{id} 返回 no-store 并检查跨源请求。面板明确区分删偏好与删证据及关联项，不宣称删除聊天日志或备份。新增与证据采用两步API，失败重试复用已创建证据；不保证跨两个请求原子性。

## 注入与权限

broker返回scene、response_policy、trace和恒为false的tool_authorization。trace只包含claim ID、选用值和排除原因；不把排除的隐私内容传模型。普通对话只渲染策略；助手把相同策略放入上下文，工具仍独立确认。

记忆关闭时broker不读偏好存储；当前输入仍可决定本轮行为。偏好不自动写入，避免今天的临时诉求永久化。

面板浏览器验证见 [test-preferences-browser.mjs](../scripts/test-preferences-browser.mjs)：隔离fixture验证新增、来源按需显示、HTML作为纯文本、保留范围修订及删除证据依赖；HTTP测试验证来源读取、跨源拒绝、no-store。浏览器fixture不等同于部署环境真模型验收。

## 验证范围与局限

测试使用独立内存或临时库。已通过：偏好存储14项、broker 9项、HTTP 4项（含删除后resume重组）、Agent Loop 8项、回应组装11项、对话接入3项、WebSocket 2项、原核心38项及语音协议检查。覆盖作用域、跨会话、当前覆盖、冲突不使用、证据删除立即生效、禁用零读取、原文不进bundle、推测不升级与重启。尚未用真实模型证明最终语气和行为完全遵守。

当前活动识别为规则而非语义分类；证据引用存在性不等于自动证明文字蕴含该偏好，管理API视为用户确认。偏好缓存被避免；应用确认接口恢复暂停的Agent Loop前，会基于原会话ID重新组装上下文，不沿用旧偏好快照。底层loop的直接调用者仍需提供新context，应用统一使用service.resume入口。历史checkpoint中旧内容的物理清除仍属于跨域删除扩展，不与停止未来使用混淆。
