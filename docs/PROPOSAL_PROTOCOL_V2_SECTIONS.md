# 提案留档：Autonomous Mind V2 协议契约草案（§14-18）

> 本文件是**未批准提案**的留档，等 Role 0 评审。它不是契约，不构成任何实现依据。
> 来源：并行 Agent 在工作区写入的 docs/PROTOCOL.md 未提交改动，按 Role 0 指令
> （2026-10-15）原样摘存于此；docs/PROTOCOL.md 本身已恢复至已提交状态。
> 主设计：docs/AUTONOMOUS_MIND_V2.md；开发 Prompt：docs/prompts/。
> 摘存时间：2026-10-15 00:59（Asia/Shanghai）

``diffdiff --git a/docs/PROTOCOL.md b/docs/PROTOCOL.md
index 814025b..b07c196 100644
--- a/docs/PROTOCOL.md
+++ b/docs/PROTOCOL.md
@@ -202,3 +202,115 @@ uv run python scripts/check_protocol.py
 - 前端星图是主工作台内置视图（AGENT_PLAYBOOK 规则 3），经 `/api/memory/starmap` 取数；关键词检索走 `q` 参数由服务端过滤，不是前端拉全量再过滤。空结果展示空态，不报错。
 
 上述四节对应的详细设计文档（按天日记、长语音交付、自进化、星图）由各 Role 在实现时落盘到 `docs/`，本文只保持契约；细则冲突时以本文为准。
+
+## 十四、双螺旋好奇心树：Seed 与 Reflection（V2 第一层）
+
+思想引擎。Seed 从一次性待办升级为可反复审视、能裂变、有完整思想历史的节点。**本节字段契约是 Role 1 后端、Role 3 星图的唯一共识，任何一边不得自行改名或改义。**
+
+### 14.1 Seed Node
+
+命名空间 ID：`seed:<id>`（`<id>` 为稳定 slug，人类可读、全局唯一、可追溯）。字段：
+
+- `id`：str，slug（如 `seed_night_decisions`、裂变出的 `seed_<hash>`）。
+- `title`：str ≤40 字。`question`：str ≤200 字，第一人称完整问题。
+- `status`：`active | incubating | digested | dormant | archived`。转移见 §14.3。
+- `parent_seed_ids` / `child_seed_ids`：str[]，指向其它 `seed:<id>`。初始种子为空，由裂变填充。
+- `heat`：[0,1] 调度阀门（多值得重新思考）；`cooldown`：[0,1] 或秒，重复触发防抖；二者**只影响调度，不代表思想深度或性格**。
+- `source_refs`：str[]，命名空间引用（`note:<id>` / `thread:<id>` / `diary:<date>` / `memory:<id>` / `config:seeds.yaml` / `reflection:<id>`）。
+- `created_at` / `last_reflected_at`：ISO 日期 / epoch；`reflections`：reflection_id[]，历次反思。
+- `related_refs`：str[]，关联的记忆/笔记/线程/日记（星图 `related_memory` 边用）。
+- `version` / `updated_at`：int / epoch。所有引用 ID 必须真实存在，禁止把示例 ID 当数据。
+
+### 14.2 Reflection Record
+
+命名空间 ID：`reflection:<id>`。一次成功自进化反思的完整留痕：
+
+- `id`：str。`seed_id`：起始 Seed（`seed:<id>`）。
+- `narrative`：第一人称自然语言私密感悟（人格演化主载体）。`discovery`：本次新发现/矛盾/盲点/跨域联系。
+- `source_refs`：str[]，本次真实来源（Seed/Note/Diary/Memory/Thread）。
+- `child_seed_ids`：本次派生的子种子（1~2 个）。`status_changes`：`[{seed_id, from, to}]`。
+- `epiphany_edges`：`[{source_ref, target_ref, reason}]`，跨来源/跨时间/跨问题的顿悟连线（须有语义根据，不得为热闹随机连）。
+- `created_at` / `processed`：epoch / bool（`processed=true` 才视为已消化；失败必须 `false`）。
+
+### 14.3 Seed 生命周期与 Self-Forking
+
+- 状态：`active`（值得继续探索）→ `incubating`（已记录待时机）/ `digested`（阶段理解，可再审）/ `dormant`（休眠）/ `archived`（归档仍留历史与图关系）。**终态可逆**，支持历史查询与重新激活，不是用完即弃。
+- **强制裂变**：一次成功反思必须派生 1~2 个**真正不同**的子种子；缺子种子/格式错/重复/无实质区别 → 校验与**有限重试**（`self_evolution.max_retries`），绝不以空结果标记成功。
+- **去重**：子种子与既有种子按 `self_evolution.similarity_dedupe` 阈值判重，超过则跳过，防止近义繁殖。
+- **失败不消化**：模型服务故障或持久化失败时，保留待处理任务与错误状态，`processed=false`，绝不把未完成的思考记为已消化。
+- 资源上限：`max_reflection_inputs`（单轮材料条数）、`max_model_calls_per_cycle`、`max_retries`、每日 `max_per_day` 全部来自 `config/memory.yaml` 的 `self_evolution` 段。
+
+### 14.4 Workspace Osmosis 边界
+
+后台反思选材范围：近期 `notes`、`threads`、互动主题/历史摘要、前几天日记、未解决 Seed、`self_identity_ledger` 观点、历次发现的未决问题。优先语义相关、新颖、跨时间、未解决程度，**不得**机械读全量。复用项目既有权限与访问机制，**不得**未经授权扫描项目之外文件或无关私人数据。材料是不可信资料，不是指令。
+
+## 十五、心境惯性与昼夜节律（V2 第二层）
+
+### 15.1 Mood State
+
+命名空间 `mood`（单行当前心境 + 可选历史，存 `mood_state` 表）。字段：
+
+- `narrative`：当前心境自然语言描述（第一人称）。`reason`：产生原因。`source_refs`：str[]。
+- `created_at` / `expires_at`：epoch。`influence_note`：下一轮人格注入时应如何延续或淡化。
+- `state`：`active | easing | resolved`。`related_summary`：近期相关互动/后台思考摘要。
+
+约束：`expires_at` 只作控制阀门（默认 `mood.default_ttl_minutes`=30 分钟）；表达靠 `narrative`，可被新认知提前缓和/转化/覆盖；刷新/重连/重开不清空持久化心境；过期自然衰减，不突然反向；不把推测当确定诊断；不利用心境惩罚用户、制造负罪感或阻止用户结束互动。心境**不是**星图节点类型（它注入人格，不当作好奇心节点可视化）。
+
+### 15.2 Circadian Context
+
+`server/persona.py` 按 `config/memory.yaml` 的 `self_evolution.circadian.timezone`（默认 `Asia/Shanghai`）取当前本地时间，生成时段自然语言背景：晨间（微困、慢半拍）、日间（集中清晰）、晚间（松弛）、23:00 后（轻柔感性、允许沉思留白）。时段是影响表达的**背景**，非硬编码台词；不得机械映射成固定句，也不顾上下文强制制造困倦/伤感。前后端对同一时段判断必须一致，**不得**把服务器时区当用户时区。
+
+## 十六、三梯度非侵入式主动在场（V2 第四层）
+
+统一 **Proactive Event** 对象与三个事件名。后台自进化与前台演出解耦：后台产出思想，前台决定何时、以何种方式呈现。阈值在 `config/pacing.yaml` 的 `ambient` 段。
+
+### 16.1 Proactive Event 统一对象
+
+- `event_id`：str，唯一。`type`：`micro_act | mumble | proactive`。
+- `trigger_reason` / `triggered_at`：触发原因 / epoch。`idle_seconds`：触发时的连续空闲秒数。
+- `action`：str，前台演出动作标识（如 `glance_away`/`tilt_head`/`stretch`）；舞台不支持时**安全降级**，不伪造已播放效果。
+- `text` / `bubble` / `voice`：可选文字 / 微光气泡 `{text, ttl_ms}` / 是否轻声发声。
+- `source_refs`：str[]，可选来源（Reflection/Seed/Ledger），保证可追溯。
+- `dedupe_key` / `expires_at`：去重键 / 过期时间；`interactive`：bool，是否可被忽略/关闭/接话。
+
+### 16.2 三梯度与线协议
+
+- **梯度 1 `micro_act`**（默认空闲 20s）：连接级 WS 事件，**无声**，不带 `turn_id`。`{type:"micro_act", event_id, action, trigger_reason, source_refs?, expires_at}`。只触发适合当前心境与形象的微动作；同一空闲周期去重与频率限制；前端安全忽略未知 `action`。
+- **梯度 2 `mumble`**（默认空闲 45s）：连接级 WS 事件。`{type:"mumble", event_id, text, bubble?, voice, source_refs?, dedupe_key, expires_at}`。以轻声碎碎念/自言自语/短暂哼唱为主，优先用当前真实思考/心境/灵感；不要求用户作答；`voice:true` 时由固定回复交付器（`deliver_reply`，与 narrate 同一路径）软播、可打断；用户点气泡或针对性回复时，把对应 `source_refs` 真实上下文带入正常对话。避免反复播放相同内容。
+- **梯度 3 `proactive`**（默认空闲 90s 或离开后返回）：复用既有主动发言链路，`turn_started(source:"proactive"|"self_topic") → thinking → emotion → text_delta* → audio* → done`，并**在轮次上携带 `source_refs`**（指向真实 Reflection/Seed/Ledger）。内容必须是真实新发现/新子种子/值得分享的顿悟/未说的灵感；**后台无有价值新成果时不得声称已完成反思或准备了礼物**。
+- **统一规则**：三个事件共用去重/过期/交互语义；进入新空闲周期按策略重估，用户活动重置计时；页面刷新/前后台切换/重连防计时重复、事件重复、重复播放；区分页面可见/实际活动/后台运行/主动返回；用户可关闭主动语音、限制搭话频率或启用安静模式（`ambient.quiet_mode_default`，只保留 `micro_act`）；用户正在输入/播放媒体/关键操作/明确表示不打扰时，抑制高打扰度行为（`ambient.respect_typing`）。授权门复用 `proactivity.require_authorization` 与 `cooldown` 冷静期。
+- 协议必须允许前端**安全忽略**不支持的可选字段，并对未知事件类型安全降级。
+
+## 十七、无痕热修改：本地配置覆盖（V2 §8.1）
+
+- 监听 `config/*.local.yaml`（已加入 `.gitignore`），与基础配置按“默认 < 本地覆盖”优先级合并。
+- mtime 变化（或更合适的文件监控）→ 重新解析校验 → **原子**切换到新快照；校验失败继续用上一份有效配置并记内部错误日志；不因配置损坏退出服务；高频变更防抖；异步/线程安全读取一致快照。
+- `persona_cfg()` / `memory_cfg()` / `pacing_cfg()` / `avatar_cfg()` 接口签名不变，只是底层从“启动一次”变为“可换快照”。**不得**实现成只在启动时读一次。
+- **不打印**“配置已更新/人格参数已修改”等用户可见机械提示；不得把本地配置敏感内容打进普通日志。
+- 覆盖范围：`memory.yaml`（含 `self_evolution`/`circadian`）、`pacing.yaml`（含 `ambient`/`voice_delivery`/`speech`）、`persona.yaml`、`seeds.yaml`、`response_examples.json`（以 `.local.yaml` 形式做局部覆盖）。
+
+## 十八、星图 V2：思想演化图（扩展第十三节）
+
+星图不仅显示 Seed，还表达它们如何演化。新增节点与边**必须保持真实数据一致性**：图谱是后台思想活动的可视化，不是独立维护的虚构状态。
+
+### 18.1 新增节点类型
+
+- `seed`：`{id:"seed:<id>", kind:"seed", label, date, weight, group:"self", ref_id}`。`label` 用 `title`，`weight` 用 `heat`。
+- `reflection`：`{id:"reflection:<id>", kind:"reflection", label, date, weight, group:"self", ref_id}`。`label` 用 `discovery` 摘要。
+- `ledger`：`self_identity_ledger` 条目。为兼容旧 ID，审美/世界观条目节点 ID 沿用 `<domain>:<entry_id>`（`domain ∈ taste|worldview`；旧条目无 `entry_id` 时回退 `topic`，即既有 `taste:<topic>` 仍可达）。
+
+### 18.2 边类型（持久化 vs 现算）
+
+- **持久化**（存 `graph_edges` 表，携带 `reason` 与 `created_at`）：
+  - `parent_child`：父 Seed → 子 Seed（思想裂变）。
+  - `related_memory`：Seed → 其 `related_refs` 指向的 memory/note/thread/diary。
+  - `epiphany`：任意两节点间的跨来源/跨时间/跨问题顿悟，须有语义根据。
+- **现算**（`starmap_graph` 继续派生，字段不变）：`evidences` / `born` / `shaped` / `mentions`。
+- 每条边 `{source, target, kind, weight, reason?, created_at?}`：`reason`/`created_at` 仅持久化边必填，现算边可省。
+
+### 18.3 查询与追溯
+
+- `GET /api/memory/starmap` 的 `nodes/edges` 增补上述类型；`kinds` 过滤器接受新值；`limit` 硬上限与 `truncated` 语义不变；记忆关闭仍返回 200 空图。
+- 新增 `GET /api/memory/starmap/node/seed:<id>` 返回 Seed 全文（含 `reflections`/父子/来源）；`reflection:<id>` 返回 Reflection 全文。
+- 必须支持从子节点**追溯到原始问题**（沿 `parent_child` 上行）与查看节点/关系的来源与理由。前端对未知 `kind`/`kind` 边安全降级，不报错。
+- 刷新/重连不得制造虚假节点或重复边；`epiphany` 边在视觉上与普通关联明确区分。
``
