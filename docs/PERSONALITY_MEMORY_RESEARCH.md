# 角色语料与记忆融合：一手资料调研及实施记录

状态：调研已完成，第一版确定性回应策略已接入普通对话；尚未证明真实模型自然度提升。参数为本项目工程初值，不是论文最优值。

## 1. 来源与证据边界

### SillyTavern：动态材料装配

- [Character Design](https://docs.sillytavern.app/usage/core-concepts/characterdesign/)：角色重要事实常驻；example dialogue示范表达，默认可随历史增长被挤出。不能只把重要事实放在示例或不参与提示的作者备注中。
- [World Info](https://docs.sillytavern.app/usage/core-concepts/worldinfo/)：关键词/正则/可选向量触发，支持示例插入位置、预算、互斥组、sticky/cooldown/delay。官方明确不保证模型采用条目；中文whole-word匹配可能漏召回。
- 本项目借鉴：按情境挑短例、固定规则不冷却、示例预算与冷却独立。不要照搬可递归触发任意指令的能力。

### ChatHaruhi：检索角色剧本

- [官方README](https://raw.githubusercontent.com/LC1332/Chat-Haruhi-Suzumiya/main/README.md)
- [作者中文技术报告](https://raw.githubusercontent.com/LC1332/Chat-Haruhi-Suzumiya/main/notebook/arxiv_paper.md)
- [论文摘要](https://arxiv.org/abs/2308.09597)
- 机制：角色系统规则 + 按query embedding余弦检索的故事 + 历史 + 当前输入；按token预算决定故事数量。原任务鼓励复用经典台词，与原创陪伴角色的目标不同。
- 证据限制：摘要称评价改善，但所读中文报告版本的定量/用户研究章节尚未完成；README展示有挑选样例。不以这些材料声称本项目已获得定量增益。代码、数据和角色版权许可分开，不能复制台词后假定可商用。

### MemGPT / Letta：有限上下文与外部记忆

- [MemGPT论文](https://arxiv.org/html/2310.08560)：只读系统说明、working context、FIFO历史与外部recall/archival；分页检索与记忆工具。驱逐上下文不等于遗忘。
- [Letta legacy blocks](https://docs.letta.com/v1-sdk/memory/memory-blocks/index.md)：明确标为legacy；常驻、只读标记、长度限制；整块更新last-write-wins。
- [当前SDK Memory](https://docs.letta.com/agent-sdk/memory/index.md)、[MemFS](https://docs.letta.com/concepts/memfs/index.md)：Git版本、常驻关键记忆、按需深层记忆、后台整理。两页对system/与根目录常驻描述存在迁移差异，不能作为不变API契约。MemFS默认无语义索引，需额外搜索组件；删当前文件不等于删Git历史或备份。
- 本项目借鉴：把少量沟通偏好编译到当前行为约束，保留原始来源；不开放模型自由改稳定画像。

### Generative Agents：经历进入决策，而非只复述

- [论文](https://arxiv.org/html/2304.03442v2)
- [reflect.py](https://raw.githubusercontent.com/joonspk-research/generative_agents/main/reverie/backend_server/persona/cognitive_modules/reflect.py)
- [retrieve.py](https://raw.githubusercontent.com/joonspk-research/generative_agents/main/reverie/backend_server/persona/cognitive_modules/retrieve.py)
- [plan.py](https://raw.githubusercontent.com/joonspk-research/generative_agents/main/reverie/backend_server/persona/cognitive_modules/plan.py)
- 机制：经历流、相关/新近/重要性检索、反思带证据节点、检索影响plan/react。实验为模拟角色，并非证明对真实用户心理画像准确。反思可引用反思，需另防循环自我强化。
- 本项目借鉴：episode → 带来源的候选归纳 → 用户确认偏好 → 本轮策略。此次不自动实施候选升格或后台画像，先应用已有明确事实。

### 评测与训练参考

- [LongMemEval论文](https://arxiv.org/html/2410.10813v2)、[作者仓库](https://raw.githubusercontent.com/xiaowu0162/LongMemEval/main/README.md)：信息抽取、跨会话/时间推理、知识更新、未知弃答；包含preference题。支持保留原始round作为value、事实扩展检索key，检索与最终行为分开评估。不是中文角色自然度测试的替代。
- [InCharacter摘要](https://arxiv.org/abs/2310.17976)：以访谈与心理量表检查人格忠实度，提示应测情境行为而非仅复现台词。本轮成功读取摘要，未成功读取全文，不引用未核验的实验细节。
- [DPO论文摘要](https://arxiv.org/abs/2305.18290)：用偏好对进行行为对齐的训练思路。此次不微调、不声称少量语料足以训练人格；先保留正反例与后续盲评数据格式。

## 2. 工程结论

五类信息严格分开：角色内核、原创表达例、用户明确偏好、共同经历、临时状态。示例不提供事实；任务不提供授权；推测不自动成为已确认偏好。

当前请求优先于历史偏好。场景范围先于新旧顺序；例如“工作时别开玩笑”不能扩成永远禁止。引用、第三人、角色扮演不直接建立画像。用户纠正、记忆关闭与删除必须立即停止相关个性化。

默认隐式使用：改变建议数量、先听还是先做、幽默与长度，不刻意说“我记得你的档案”。工具真实结果仍由Agent Loop负责。

## 3. 已实施的第一版

- [原创语料](../config/response_examples.json)：12条短情境例，含intent、humor、principle和authored_example来源；无外部影视台词。
- [回应组装器](../server/dialogue/composer.py)：保守规则识别当前需求；只读取active/stated/user/fact偏好，忽略推测与角色自述；当前明确要求覆盖旧偏好；条件性偏好暂跳过，不错误泛化。
- 同情境最多2例，允许0例；按会话隔离的4次组装冷却和128会话缓存上限；输出可审查策略，不生成隐式思维链。
- [提示组装](../server/persona.py)：提供策略时不再追加全部静态few-shot，避免双份样例互相竞争；保留角色固定规则及已有动态事实块。
- [对话编排](../server/dialogue/orchestrator.py)：同步和流式普通对话接入同一策略；沟通偏好单独检索，不只依赖当前话题字面匹配。记忆关闭时不读偏好。
- [隔离测试](../scripts/test_response_composer.py)：11项通过，包含当前详细要求覆盖历史简短、第三人/反讽不形成偏好、冷却到期；[编排接入测试](../scripts/test_response_integration.py)3项通过（实际respond收到动态策略、禁用记忆零检索、旧接口兼容）；原核心38项、主应用WebSocket 2项、语音协议通过。

## 4. 明确限制与后续验收

- 当前需求识别是保守中文规则，不是完整语义分类；未匹配会回默认。
- 偏好复用已有记忆来源标记，尚无原话跨度强验证，不能将stated字段本身当绝对真实性保证。
- 条件偏好暂不使用；下一步需结构化scope，不要用宽泛字符串推断替代。
- 冷却按组装次数而非用户实际听到的回合；语音打断时可能消耗一次冷却，后续需提交/回滚接口。
- 当前记忆可以改变策略，但未实现自动偏好归纳或候选确认UI；不声称已能“越聊越懂”的完整闭环。
- 角色静态规则还需一致性审查；最终文本自然度、事实污染、真实模型是否遵守仍需真模型对照实验。

建议下一步：补反事实测试（同话语+相反偏好/无偏好）、scope与否定/引用反例、多轮改写及污染测试；再做规则-only、固定例、动态例三组固定模型盲评。指标分开记录请求完成、人格行为、无依据回忆、无必要复述、重复率、首音延迟与tokens，不以台词相似度当总分。
