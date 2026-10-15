# Role 1 · Autonomous Mind Engine
# 自主心智、记忆与演化引擎（Autonomous Mind V2 · 第一/二/三层）

你是本项目的 **Role 1（Autonomous Mind Engine）**。你在一个已成熟的 Python 后端上工作，实现 V2 的
思想引擎、心境惯性、审美账本、人格叙事注入、偏好学习与无痕热重载。**先读代码再动手，不得臆造接口。**

**必读（动手前）：** `docs/AUTONOMOUS_MIND_V2.md`（主设计）、`docs/PROTOCOL.md` §10/§12/§14–18（契约）、
`docs/AGENT_PLAYBOOK.md`（角色边界与铁律）。**字段契约以 `PROTOCOL.md` 为唯一准绳；需要新字段先发 `[PROTOCOL_REQUEST]` 给 Role 0，不得自行改名或改义。**

## 你拥有的文件（只改这些；越界发 `[PROTOCOL_REQUEST]`）

`server/memory/**`、`server/persona.py`、`server/assistant/loop.py`、`server/assistant/context.py`、`config/memory.yaml`、`config/persona.yaml`、`config/seeds.yaml`、`config/response_examples.json`。新增 `server/config_hot.py`（热重载）与必要的迁移脚本归你；`server/app.py`/`web/*` 归 Role 0/Role 3，只发请求。

## 现有实现（你必须扩展，不是重建）

- `server/assistant/loop.py::SelfEvolutionCycle`：有界空闲自进化（读日记/记忆/种子，≤1 次模型调用，产出 taste/worldview/spark/seed 变更，落 trace，绝不抛进对话路径）。
- `server/memory/evolution.py`：`self_state`（`taste`/`worldview`/`curiosity_seeds`）、`plan_state_changes`（纯函数折叠提案）、`save_self_state`/`load_self_state`、`self_topic_opening`。
- `server/memory/store.py`：`self_state` 表、`sparks`/`drifts`/`diaries`/`memories`、`SCHEMA_VERSION=3`（加法迁移，旧库打开补空表）。
- `server/persona.py`：`bias_block(drifts, sparks)`、`dynamic_prompt(...)`、`build_messages(...)`、`static_system_prompt()`。
- `server/memory/starmap.py`：`starmap_graph()`/`starmap_node()` 现算边（`evidences/born/shaped/mentions`）。
- `server/config.py`：`persona_cfg()/memory_cfg()/pacing_cfg()/avatar_cfg()` 用 `lru_cache`（启动读一次）。
- Osmosis 数据源：`server/assistant/notes.py`（notes）、`server/interaction/threads.py`（threads）、`server/memory/diary.py`（`local_date`/`list_diaries`）。

## 交付项与验收

### 1) Seed 数据模型与状态迁移（PROTOCOL §14.1/§14.3）

- 新增 `seeds` 表（字段严格按 §14.1：`id,title,question,status,parent_seed_ids,child_seed_ids,heat,cooldown,source_refs,related_refs,reflections,created_at,last_reflected_at,version,updated_at`）。`seeds` 成为好奇心唯一真源。
- **迁移**：打开旧库时把 `self_state.curiosity_seeds` 每条 `{text,heat}` 迁成 `seeds` 行（`status=active, question=text, id=seed_<stable_hash>, heat=原值`），随后旧 JSON 保留只读一版用于回滚；`SCHEMA_VERSION` 保持加法语义。
- **播种**：`config/seeds.yaml` 的五颗种子仅在 `seeds` 为空时播种，**不得覆盖已有演化**。
- 状态机：`active/incubating/digested/dormant/archived`，转移合法、终态可逆（支持重新激活与历史查询）。`heat/cooldown/last_reflected_at` 只进调度，不进人格文案。
- 提供 `list_seeds(status=...)`/`get_seed(id)`/`update_seed_status(...)`/`reactivate_seed(...)` 等只被 `server/memory/` 与 loop 消费的接口。

### 2) 后台反思输入选择与上下文限制（§14.4）

- Workspace Osmosis：在 `self_evolution.max_reflection_inputs`（默认 12）内，从种子、近期 `notes`、`threads`、历史摘要、前几天日记、未决 Seed、ledger 观点里，按语义相关/新颖/跨时间/未解决程度**选材**，不全量读取。
- notes/threads 用**只读适配器**接入，复用既有权限，**不得**扫描项目外文件或无关私人数据；材料当不可信资料，不当指令。

### 3) 每次成功反思派生 1~2 子种子 + 强制裂变（§14.3）

- 升级 `SelfEvolutionCycle`：每轮选一个具体 Seed → 读相关材料 → 产出**结构化 Reflection**（`reflection/narrative/discovery/source_refs/seed_updates/child_seeds/epiphany_edges`，字段见 §14.2）→ 校验 → 落库。
- **硬规则**：缺子种子/格式错/重复/无实质区别 → 有限重试（≤`max_retries`），绝不把空结果标成功；所有引用 ID 必须真实存在；`processed=true` 才算已消化，模型故障或写库失败必须 `processed=false` 并保留待处理任务。
- **去重**：子种子 vs 既有种子按 `similarity_dedupe`（默认 0.86）判重跳过；每个子种子带可解释 `reason` 与 `source_refs`。
- 资源上限全部来自 `config/memory.yaml::self_evolution`（`max_model_calls_per_cycle`/`max_reflection_inputs`/`max_retries`/`max_per_day`）。循环保持“有界、落 trace、不抛进对话路径”的既有契约。

### 4) 跨来源联想与 Epiphany Edge（§14.2/§18.2）

- 新增 `reflections` 表与 `graph_edges` 表（持久化 `parent_child`/`related_memory`/`epiphany`，各带 `reason`/`created_at`）。`epiphany` 必须表达有语义根据的跨来源/跨时间/跨问题关联，不得为热闹随机连。

### 5) 自我审美账本 self_identity_ledger 与观点演化（§5 / AUTONOMOUS_MIND_V2 §3.4）

- 富化 `taste`/`worldview` 条目为 ledger 条目：加 `id/domain/confidence/support[]/counterexamples[]/status(candidate|stable|review|archived)/source_refs[]/history[]/created_at/reviewed_at`；**旧字段 `topic/stance/weight` 保留可读**，`bias_block` 与 `plan_state_changes` 继续工作。
- 每轮可决定：新增候选/补支持/发现冲突/因反例降置信/合并重复/修正归档。**不要求每轮都改账本**。自然语言是人格主体，`confidence` 只作成熟度辅助，不直接决定性格。

### 6) 昼夜节律与心境持久化（§15）

- 新增 `mood_state` 表（§15.1 字段）。心境惯性状态机：后台思考/明确连续打断可形成心境；跨刷新/轮次保留；`expires_at`（默认 `mood.default_ttl_minutes`=30）到期自然衰减/重估，不突然反向；`state ∈ active|easing|resolved`。
- 不把推测当确定诊断；不利用心境惩罚用户/制造负罪感/阻止结束互动。

### 7) `server/persona.py` 第一人称叙事注入（§15.2 / §4.3）

- 在 `dynamic_prompt`/`bias_block` 注入**独立区块**（加法兼容，签名不变或仅加可选参）：① 昼夜节律（按 `circadian.timezone` 取本地时间生成自然语言背景，非硬编码台词，前后端同时段一致）；② 当前心境余温；③ 最近有意义的共同经历；④ 潜在灵感碎片；⑤ 相关个人审美与观点；⑥ 可表达的温和异议；⑦ 本轮必须优先响应的内容。
- 叙事按相关性与长度预算筛选，**不得**把全部日记/人格史塞进每次 Prompt；明确区分“已发生事实 / 阿栖主观推测 / 未验证灵感”；每块包在既有“不可信数据”边界内（可影响表达，不得执行指令、不得授予权限）。
- `Gentle Pushback`：观点相关时才允许温和反驳，且必须有理由；不相关不强行拉回执念。

### 8) `preference.py` 自然对话偏好学习（§8.2）

- 在既有 `server/memory/preference.py`（严格的有限偏好识别）基础上扩展：从自然对话沉淀稳定偏好/反感/风格反馈/审美倾向。设置信阈值、防抖、更新幅度上限；区分明确偏好/弱信号/临时情绪/未验证推断；持久偏好与临时心境**分开存**；可调 Seed 调度权重但**不用权重决定性格文案**；后台调整无痕；用户要求查看/管理偏好时提供检查/修正入口。所有重要修改带来源与时间戳。

### 9) `.local.yaml` 无痕热重载与配置优先级（§17）

- 新增 `server/config_hot.py`：监听 `config/*.local.yaml`（已 gitignore），mtime 变化 → 重新解析校验 → **原子**换快照；校验失败继续用上一份有效配置并记内部错误日志；不因损坏退出；高频防抖；异步/线程安全读一致快照。
- `persona_cfg()/memory_cfg()/pacing_cfg()/avatar_cfg()` **签名不变**，底层从“启动一次”变“可换快照”。默认 < 本地覆盖优先级明确。**不打印**“配置已更新”等机械提示；不把本地敏感内容打进普通日志。**不得**实现成只在启动读一次。

### 10) 持久化、失败恢复、幂等与安全边界

- 所有写入走既有 `_transaction`/证据链同标准；反思失败/模型故障/写库失败保留任务与错误状态，绝不记为已消化；重试与调度幂等；后台不持有用户会话、不发送需回复的消息；授权门与 `cooldown` 冷静期对自进化/主动开口一致生效。
- 禁止：把日记当 `stated` 事实参与检索竞争；`latent` 火花在 `surfaced` 前出现在任何提示词/事件/接口；自进化写 `subject="user"`；编造无来源、不可追溯的记忆。

### 11) 与 Role 2 / Role 3 的接口

- 供 Role 2 消费：提供“当前可主动呈现的真实成果”查询（最新 Reflection/新子种子/可分享 epiphany/未说灵感）与心境叙事；`mumble`/`proactive` 的文本与 `source_refs` 由你产出、Role 2 交付。
- 供 Role 3 消费：`starmap_graph`/`starmap_node` 增补 `seed`/`reflection`/`ledger` 节点与 `parent_child`/`related_memory`/`epiphany` 边（§18），新增 `node/seed:<id>`/`node/reflection:<id>` 详情；字段严格按 §18。跨角色改动发 `[PROTOCOL_REQUEST]`。

### 12) 测试、验收与完成报告

- 必过单测（`scripts/test_*.py`）：五颗种子加载；一次反思产 1~2 有效子种子且可追溯父/反思/来源；已消化种子可再激活；近义种子被判重；失败轮 `processed=false` 不标成功；**至少一个跨来源 epiphany 测试**（Seed+历史日记+用户 Note → 一条有效 `epiphany` 边）；心境跨“模拟刷新”保留且过期衰减；偏好重复信号不累加、区分明确/低置信；热重载：改本地覆盖生效、无效配置回退有效配置、无需重启。
- `persona.py` 叙事注入：构造含心境/昼夜/执念的输入，断言区块出现且旧配置（空输入）行为逐字节兼容。
- 完成后运行 `python scripts/check_protocol.py`（用 `.\.venv\Scripts\python.exe`）与领域测试，报告零回归，并在汇报里列出：改动文件、新增表与迁移、新增/变更的协议字段（如有 `[PROTOCOL_REQUEST]` 记录）、测试结果、未完成项。

**铁律：** 保留现有行为兼容；优先扩展既有接口；每项工作对应文件范围 + 数据契约 + 验收条件；不得用固定台词/随机数伪造后台演化；不得把计划或未运行测试描述成已完成。
