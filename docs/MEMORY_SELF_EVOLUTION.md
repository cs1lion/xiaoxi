# 记忆即自进化：小溪的效用记忆与程序性记忆（2026-10-17）

本文件记录这一轮的设计依据、实现边界和验证口径。结论先说清楚：

> **自进化不是"让模型多反思几次"，而是让记忆层具备三件事：从结果里学（utility）、把经验变成可执行的做法（procedural lessons）、按证据合并与退役（grow-and-refine）。** 没有这三件，反思只会产出一堆情绪描述；有了这三件，反思才会改变下一次对话。

本轮只改记忆/自进化层与它们进入 Prompt 的那一条路径。没有新增 WS 事件、没有改 REST 字段、没有换向量库、没有引入模型微调。

## 1. 为什么是这三块

当前实现已经很强的地方：写入有原话证据链、同主题独立事实不互相覆盖、偏好有作用域与退役守卫、检索为空会显式注入负向约束。短板也很明确：

1. **检索是静态的**：一条记忆只要文本相似、够重要、够新，就会一直排在前面；它过去被证明有用还是有害，系统不知道。MemRL 把这个问题叫 **semantic trap**——看起来相关，历史上却总是帮倒忙。
2. **自进化只长"自我"，不长"做法"**：`self_state` 里的 taste / worldview / spark 决定她"是谁"，但决定不了她"下次遇到同样情境该怎么做"。反思产出的东西没有一条会改变对话策略。
3. **没有合并/退役机制**：相似经验会换措辞繁殖，错误经验没有证据把它淘汰。

对应地，本轮做了三件事，全部落在 SQLite 与既有模块里：

| 能力 | 研究来源 | 本仓库落点 |
|---|---|---|
| 结果驱动的记忆效用（Q 值） | MemRL | `server/memory/feedback.py`、`memories.utility*`、`memory_usage` 表 |
| 二阶段检索（相关性门槛 → 效用重排） | MemRL | `server/memory/retriever.py::_utility_factor` |
| 程序性记忆（when X, do Y） | EvolveR / ACE / MemSkill | `server/memory/lessons.py`、反思 Prompt、动态提示词 |
| 逐条计数、近义合并、按证据退役 | ACE / MemSkill | `lessons.helpful/harmful`、`record_lessons`、`prune` |
| 来源可追溯 | 既有 V2 反思契约 | `evidence_refs` 必须命中本轮真实材料 ref |

## 2. 研究里真正被采纳的部分

### 2.1 MemRL：把"用过之后有没有用"写回记忆

[MemRL](https://arxiv.org/abs/2601.03192) 给每条经验存三元组 `(intent, experience, Q)`，检索分两阶段：

1. 语义召回：相似度超过阈值，取 top-k₁；
2. 价值选择：`score = (1-λ)·sim + λ·Q`，只对召回池重排，取 top-k₂。

奖励只更新**实际注入上下文**的那几条：`Q ← Q + α(r - Q)`。

小溪的适配：

- 检索侧保留现有相关性门槛（`min_similarity` / `min_score`）作为第一阶段；通过门槛后，再用 `Q` 做**有界乘子** `1 - w + 2w·Q̂`（默认 `w=0.30`，即 ±30%），而不是把 Q 直接加到相关性上。原因是陪伴场景里"相关"必须压过"历史上偶尔好用"，不能让一条旧经验靠 Q 把不相关内容捞进来。
- `Q̂` 按观测次数向 0.5 收缩：`(Q·n + 0.5·prior) / (n + prior)`，`prior=3`。一次夸奖不会把记忆捧上天，一次纠正也不会把它埋掉。
- 奖励只来自下一轮用户的**明确**确认/纠正。沉默、继续聊别的、"嗯"都不是奖励；这类轮次只落 `neutral`，`utility_n` 不变。

这套映射保留了 MemRL 的核心（outcome-driven reranking），但没有照搬它的任务奖励——小溪没有任务执行器，不能假装有 ground truth。

### 2.2 EvolveR / ACE / MemSkill：把经验蒸馏成"做法"

- [EvolveR](https://arxiv.org/abs/2510.16079)（ICML 2026）：经验轨迹 → 抽象原则库，在线时检索原则指导决策。
- [ACE](https://arxiv.org/abs/2510.04618)（Agentic Context Engineering）：把上下文维护成 playbook，条目级 helpful/harmful 计数、增量 delta、grow-and-refine；它明确指出整体重写会导致 context collapse 和 brevity bias。
- [MemSkill](https://arxiv.org/abs/2602.02474)：把抽取/合并/剪枝当成可演化的 memory skills，由 selector 选、executor 用、designer 定期从失败案例里改技能库。

小溪的适配是一个很小的条目库，而不是第二套人格：

```json
{
  "trigger": "当用户在深夜说累时",
  "action": "先接住情绪，不急着给方案",
  "cues": ["好累", "深夜"],
  "evidence_refs": ["diary:2026-10-13"]
}
```

- 由 `SelfEvolutionCycle._reflect` 在同一次反思调用里顺带产出（0~2 条），不新增模型调用。
- `evidence_refs` 必须命中本轮真实读到的材料，否则丢弃；不因为一条坏 lesson 让整个反思失败。
- 近义条目按 `dedupe_similarity=0.72` 合并，证据取并集。
- `harmful >= 2 且多于 helpful` → 退役；活跃条目超过 `max_active` → 按 Laplace 有用度淘汰最低的。
- 注入走独立的 `render_lessons` 块，带明确的"只是倾向、不是用户事实、不得改变安全规则/授权"边界；每轮最多 3 条、320 字符。

### 2.3 没有采纳的部分（以及为什么）

| 方向 | 代表工作 | 本轮不做的理由 |
|---|---|---|
| 权重级自进化（SEAL 类） | 自适应微调 | 成本、可逆性、审计性都不适合日常陪伴；也违反本项目"记忆硬删除可追溯"的取向。 |
| 元演化整个记忆架构 | [MemEvolve](https://arxiv.org/abs/2512.18746)、[EvoMemBench](https://arxiv.org/abs/2605.18421) | 需要可重复的 benchmark 与多次架构搜索；小溪当前连稳定的真实交互样本都还不够。先把手上的记忆层做成可测的。 |
| 全量 ADD-only + 实体图 | Mem0 2026 新算法、Zep/Graphiti、A-MEM | 方向正确，但会牵动写入契约、证据链和星图。等本轮 utility/lesson 跑出真实数据，再决定是否把关联图接入检索。 |
| 每轮 LLM 裁判给奖励 | 各类 RL memory 工作 | 每轮多一次模型调用，成本和延迟都不划算；先用确定性、保守的显式反馈。 |

## 3. 实现细节与不变量

### 3.1 数据层

`memories` 新增：

- `utility REAL NOT NULL DEFAULT 0.5`
- `utility_n INTEGER NOT NULL DEFAULT 0`
- `last_reward_at REAL`

`memory_usage`：每个注入过记忆的用户轮次一行，记录 `session_id`、`user_message_id`、`memory_ids`、`lesson_ids`、结算状态、奖励与逐条明细。它同时是学习记录和审计记录：为什么这条记忆的 Q 变了，可以回溯到哪一轮、哪句话。

`lessons`：`trigger`、`action`、`cues`、`scope`、`source`、`evidence_refs`、`helpful`、`harmful`、`status`、`use_count`。

Schema 版本从 3 升到 4；迁移只加列/加表，不猜旧语义。

### 3.2 反馈结算

`MemoryManager.observe_turn` 在本轮抽取之前调用 `feedback.settle_pending`：

- 找该 session 里所有 `settled_at IS NULL` 且 `user_message_id < 当前消息` 的轮次；
- 用**紧邻的下一条用户消息**作为反馈，而不是"当前消息"（多条积压时不会错配）；
- 超过 `max_settle_age_hours`（默认 12h）的轮次记 `expired`，不给奖励；
- 记忆归因用字符二元组包含度，只有用户话里确实提到该记忆内容才更新；多候选且无法归因时不动；
- lesson 只响应**说话方式**的明确反馈（"不用安慰我""太啰嗦"），事实纠正不会误伤风格条目。

整个过程在异步抽取链路里，不进入语音快环。

### 3.3 检索

`MemoryRetriever.search` 仍然先算原来的 `similarity + importance + recency`，先过 `min_similarity`/`min_score`，再乘 `utility_factor`。因此：

- `utility_n=0` 时因子恒为 1，旧库行为完全不变；
- 效用不能创造相关性；
- 效用最多 ±30%，相关性仍是主导项。

### 3.4 反思与注入

- `REFLECT_SYSTEM` 增加可选的 `lessons` 字段；只允许 0~2 条，要求具体情境 + 可执行做法 + 匹配关键词 + 材料 ref。
- `validate_reflection_payload` 对 lessons 做独立校验：坏条目丢弃，反思本身照常通过。
- `_reflect` 只在反思成功（有有效子种子）后落库，失败轮次不产生副作用。
- Orchestrator 在 `_turn_context` 里按当前用户话检索 lessons，放进 `data['procedural_lessons']`；`_render_turn_context` 渲染成独立的 system 段；`mark_used` 同时记录 memory 和 lesson 的使用，供下一轮结算。
- 记忆关闭时，lessons 也不注入。

### 3.5 硬删除仍然彻底

`memory_usage` 和 `lessons.evidence_refs` 只存 id，不存记忆正文。任何 `delete` / `delete_all` / `delete_key` 之后，`store._scrub_deleted_indexes()` 会把已不存在记忆的 id 从 usage 明细和 lesson 证据里清掉；轮次审计本身保留，但无法再反查被删除的记忆。这与 `config/memory.yaml` 的 `delete_semantics: hard` 一致。

## 4. 配置

`config/memory.yaml`：

```yaml
retrieval:
  utility_weight: 0.30
  utility_prior_strength: 3.0

feedback:
  enabled: true
  utility_alpha: 0.30
  utility_prior_strength: 3.0
  max_settle_age_hours: 12
  min_overlap: 0.12

lessons:
  enabled: true
  max_active: 24
  max_inject: 3
  max_inject_chars: 320
  min_similarity: 0.12
  dedupe_similarity: 0.72
  retire_harmful: 2
```

缺省值在代码里是关闭的（`feedback.enabled=False`、`lessons.enabled=False`），所以旧配置/旧部署行为不变；仓库内配置显式打开。

## 5. 验证

```bash
.\.venv\Scripts\python.exe scripts\test_memory_self_evolution.py
.\.venv\Scripts\python.exe scripts\test_memory_orchestrator.py
.\.venv\Scripts\python.exe scripts\test_memory_retrieval.py
.\.venv\Scripts\python.exe scripts\test_memory_store_contract.py
.\.venv\Scripts\python.exe scripts\test_self_evolution_quality.py
```

新增测试覆盖：

- 效用重排只在同等相关性下起作用，不能把不相关记忆捞进来；
- 明确确认/纠正/沉默/过期四种反馈；多候选不误伤；结算幂等；
- lesson 的证据可追溯、注入攻击/命令式 trigger 被拒、近义合并、有害退役、容量上限、cue/相似度检索、渲染预算；
- 反思带 lesson 时通过，不可追溯的 lesson 被丢弃但不失败；
- 真实 Orchestrator 路径：lesson 注入、使用计数、下一轮风格抱怨使其 `harmful+1`。

## 6. 已知边界与下一步

1. **奖励稀疏**：明确说"你记得没错"的用户是少数。下一步可以把慢环评估器的结论作为批量奖励来源，但不应每轮加一次模型调用。
2. **lesson 质量未过真实模型抽样**：机制层已钉死；语义质量仍需按 `EVOLUTION_QUALITY_EVAL.md` 的方法，在真实 LLM 下抽样复核（来源相关性、具体性、可执行性、无空洞）。
3. **检索仍是词法**：本轮没有引入 embedding；等 utility 数据稳定后，再比较"词法 + 语义 + 图扩展"三组，而不是先换向量库。
4. **时间有效性仍不完整**：`valid_from/valid_until` 与"去年在哪工作"类查询仍是 `MEMORY_RESEARCH_NEXT.md` 里的未完成项。
5. **lessons 还没有管理界面**：目前只在内部注入，不进星图/记忆面板。若要暴露，需要 Role 0 在 `PROTOCOL.md` §13 增补节点类型并补前端渲染；不要把内部表直接塞进现有节点结构。
6. **没有元演化**：记忆策略本身（阈值、权重、淘汰规则）还不由数据自动调整；那需要稳定的评测集，先不做。

## 7. 一手资料

- [MemRL: Self-Evolving Agents via Runtime Reinforcement Learning on Episodic Memory](https://arxiv.org/abs/2601.03192)
- [EvolveR: Self-Evolving LLM Agents through an Experience-Driven Lifecycle](https://arxiv.org/abs/2510.16079)（ICML 2026）
- [Agentic Context Engineering: Evolving Contexts for Self-Improving Language Models](https://arxiv.org/abs/2510.04618)
- [MemSkill: Learning and Evolving Memory Skills for Self-Evolving Agents](https://arxiv.org/abs/2602.02474)
- [MemEvolve: Meta-Evolution of Agent Memory Systems](https://arxiv.org/abs/2512.18746)
- [EvoMemBench: Benchmarking Agent Memory from a Self-Evolving Perspective](https://arxiv.org/abs/2605.18421)
- [Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory](https://arxiv.org/abs/2504.19413)
- [A-MEM: Agentic Memory for LLM Agents](https://arxiv.org/abs/2502.12110)
- [Zep: A Temporal Knowledge Graph Architecture for Agent Memory](https://arxiv.org/abs/2501.13956)
- [LongMemEval: Benchmarking Chat Assistants on Long-Term Interactive Memory](https://arxiv.org/abs/2410.10813)

以上论文的分数均为作者报告，本仓库未复现；本轮采纳的是机制，不是结论。真实效果以本项目的离线门禁与后续 live 抽样为准。
