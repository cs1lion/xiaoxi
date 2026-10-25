# Spec：Pixel World Engine（v0.1，待 grill-with-docs refine）

**状态**：草案，等待 /grill-with-docs 第二轮细化。
**目标**：全新子项目 `pixel-world-engine`，专注"真实运行的像素世界"；旧项目存档备用。
**来源**：用户 2026-10-24~10-25 的设计对话 + 《千与千寻》美术设定集提炼。

---

## 一、一句话目标

造一个**由 LLM 规则生成驱动、潮汐灯油为核心机制、记忆以时间线 MD 存储**的像素世界引擎。用户说一句话（或什么都不说），LLM 按世界法则输出结构化的"命令列表"→世界自己跑起来。

---

## 二、核心设计原则

### CP1：规则优先，模型兜底

- **L1 硬规则**（JSON Schema）：不可违反的世界法则（白名单、取值域、数量限制）。
- **L2 规则模板**：供模型按主题选择（`weather.set('rain')`、`tide.rise(0.3)`、`building.add({ prefabId, at })`）。
- **L3 自由描述**：补充上下文（剧情/情绪），只用于叙事层，不直接改世界状态。
- **失败策略**：模型输出不符合 Schema → 拒收，返回错误码（不是"忽略"）+ 一句解释。

### CP2：记忆即时间线（MD + YAML）

- **目录结构**：
  ```
  memory/
    entries/YYYY-MM-DD.md          # 每日快照
    characters/*.md               # 单人档案（双面表、日程）
    events/E####-*.md             # 单次事件记录
    rules/*.yaml                  # L2 社会规则库
  index.yaml                       # 索引
  ```
- **写入规范**：
  - 强制 source（player_action / npc_event / llm_generation / direct_observation）。
  - 必须带证据链："她记住了你怕黑" → "因为她上次在你没点灯时主动递了灯笼"。
  - 拒绝脑补：无明确动作依据时，不假设角色感受或偏好。

### CP3：潮汐是动态表现（规律本身是惊喜）

- **三档动画**：涨退（宏观水位 + 低区淹没）、波浪（水面波纹循环）、涟漪（单个格子效果）。
- **周期随机抖动**：12 分钟一轮 ±2 分钟，NPC 作息随潮位调整，隐藏区域仅在特定潮位出现。
- **实现优先级**：先做涨/退两档基础版 → 再加波浪/涟漪。

### CP4：灯油 = 寿命（纯渲染层映射台账耗时）

- **亮度衰减**：灯越暗 → 角色发灰、动作变慢。
- **消耗方式**：买东西、受伤、使用技能都消耗灯油。
- **数据来源**：台账中单子的 running 时长 → 映射到对应角色的灯油余量（不改 Simulation 的确定性）。

### CP5：折返结构（终点回到起点，同一场景因玩家变化被重新演绎）

- **渡口站台**三阶段：初入（石像脏、路灯未亮）→ 工作（开始有人打招呼）→ 折返（石像擦净、路灯亮了、NPC 站在原处）。
- **视觉差异凭证**：用存档里的 `world_change_version` 字段记录"这次回来和上次有什么不同"。

---

## 三、新项目蓝图

### 仓库布局（拆分仓，与原 `xiaoxi` 并行）

```
pixel-world-engine/
├── world-client/         # Phaser 前端（复用 pixel-world/ 渲染与模拟内核）
│   ├── src/
│   │   ├── render/*      # 复用的 tilemap / buildings / villager / weather / fog
│   │   ├── world/*       # 复用的 simulation.ts / grid.ts / save.ts
│   │   ├── ui/           # HUD / Oracle / Drawer（重做纸签木牌风格）
│   │   ├── tide.ts       # 新增：潮汐状态机
│   │   └── lampoil.ts    # 新增：灯油系统
│   ├── package.json
│   └── vite.config.ts
├── server/
│   ├── app.py            # FastAPI 主服务
│   ├── world-rules/
│   │   └── engine.py     # 规则校验器（接收 LLM 输出 → 命令列表）
│   ├── world-events/
│   │   └── log.py        # 只增不改的 WorldEventLog（取代旧 ledger）
│   └── world-model/
│       └── orchestrator.py  # LLM 驱动的造物主（prompt + Schema）
├── memory/
│   ├── entries/          # MD 时间线
│   ├── characters/
│   ├── events/
│   ├── rules/
│   └── index.yaml
└── tests/
    └── unit/
```

### 从母项目拷贝/修改的模块

| 模块 | 原路径 | 新路径 | 改动幅度 |
|---|---|---|---|
| 渲染引擎 | `pixel-world/src/render/*` | `world-client/src/render/*` | 小（换色板 + 潮汐图层） |
| 模拟核心 | `pixel-world/src/world/simulation.ts` | `world-client/src/world/simulation.ts` | 中（加潮汐状态机） |
| 存档格式 | `pixel-world/src/world/save.ts` | `world-client/src/world/save.ts` | 小（新增 tide/lampOil 字段） |
| UI Shell | `pixel-world/index.html` + `/src/ui/*` | `world-client/` 同上 | 大（换成纸签木牌风格） |
| 契约层 | `pixel-world/src/world/projection.ts` | `server/world-events/log.py` | 大（事件日志取代投影） |

### 新增的核心模块

| 模块 | 路径 | 说明 |
|---|---|---|
| 规则引擎 | `server/world-rules/engine.py` | JSON Schema 校验器，接收 LLM 输出 → 命令列表 |
| 事件日志 | `server/world-events/log.py` | 只增不改的 WorldEventLog（取代旧 ledger） |
| 潮汐系统 | `world-client/src/world/tide.ts` | 潮汐状态机 + 渲染层接口 |
| 灯油系统 | `world-client/src/world/lampoil.ts` | 灯油余量 → 亮度衰减逻辑 |
| 记忆库 | `memory/` | MD + YAML 存储结构 |
| 造物主 | `server/world-model/orchestrator.py` | LLM 生成规则文本 → 校验为结构化指令 |

---

## 四、CLI 命令清单（第一期）

```
POST /api/world/orchestrate { prompt: string, rule_mode: 'strict' | 'relaxed' }
GET  /api/world/status                # 当前世界状态快照（供 LLM 读取）
POST /api/world/events/read           # 读取最近 N 条事件日志
POST /api/memory/write { entry: MD }  # 写入一条记忆
GET  /api/memory/query ?tag=X         # 检索记忆（后期上向量 DB）
```

**Command Schema（第一期）**：
- `weather.set('clear'|'rain'|'night')`
- `tide.setLevel(0.0–1.0)`
- `building.add({ prefabId, at: GridPoint })`
- `building.remove({ instanceId })`
- `miracle.spawn(kind)`
- `npc.setSchedule(id, scheduleJson)`

---

## 五、清理计划（老项目归档，不动数据）

| 文件/模块 | 原因 | 操作 |
|---|---|---|
| `web/` 中的语音栈 | 本次专注像素世界，语音暂不考虑 | 归档（不立即删除） |
| `server/dialogue/*` | 与新的世界事件系统无关 | 归档 |
| `server/task/*` | 旧的督办台账将被事件日志取代 | 归档（保留接口契约以防万一） |
| `eval/` | 旧评测脚本大多针对语音流 | 归档 |
| `docs/VOICE_*.*` | 语音相关文档 | 归档 |

**注意**：归档不等于删除，保留原始提交历史作为参考。等新项目稳定后再决定是否彻底删掉。

---

## 六、验收条件（第一阶段 MVP）

1. **LLM 能输出有效命令**：`POST /api/world/orchestrate` 能接受自然语言 → 返回合法的 Command 列表（Zod/Pydantic 校验通过）。
2. **命令能改变世界**：世界客户端收到命令后，实际触发天气/潮汐/建筑的变化。
3. **潮汐有动态表现**：涨/退两档水位动画 + 低区淹没判定。
4. **记忆可写入可读**：`POST /api/memory/write` 写入 MD 文件，`GET /api/memory/query` 能列出条目。
5. **存档升 v3**：新增 tide/lampOil 等字段，指纹测试通过。

---

## 七、待定决策（需 grill-with-docs refine）

| 问题 | 建议 | 待定 |
|---|---|---|
| 记忆检索何时上向量 DB？ | 现阶段 MD+YAML；等条目超过 100 条再评估 | ✅ |
| 是否需要多角色（现在只有莞尔一人）？ | 二期考虑；一期先保证单角色的体验完整 | ✅ |
| 是否接入真实 API Key？ | 默认 Mock，用户输入 key 后才真调用 | ✅ |

---

## 八、下一步行动

1. **执行 `/grill-with-docs`**：带着这份 spec 去 grill 一轮，确认 L1/L2/L3 规则边界、Command 清单、记忆写入规范是否合理。
2. **出票据拆分**：把 spec 拆成 tracer-bullet 票（每条边上有 blocking edge）。
3. **动手第一刀**：先建 `pixel-world-engine/` 仓库骨架 + `server/world-rules/engine.py` 原型 + `world-client/src/world/tide.ts` 基础版。

---

*本文档日期：2026-10-25。后续修订请更新版本号与日期锚。*
