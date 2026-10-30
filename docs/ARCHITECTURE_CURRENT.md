# 现状架构图与数据流图（代码核验版）

**口径**：本文件只画**当前运行态**，每一条边都在 2026-10-29 对 HEAD `ddec3b7` 从代码核过
（证据锚见文末表格与核对命令）。目标态/提案态一律不混入这里 ——
像素世界 13 态规格图在 `docs/ARCH_V2_DIAGRAM.md`，其 v2 事件标注「提案，未生效」，
与本文件是两份东西，不要互相引用为现状。

锚点纪律（AGENTS.md 写数字判据）：正文只写**符号名**不写行号（行号会漂移）；
必须引用行号处给「2026-10-29 + 符号」双锚，并附核对命令。

---

## 图 1 · 系统架构总览

```mermaid
graph TB
    subgraph 浏览器端
        W["像素世界主页 /<br/>pixel-world（Vite 构建，挂载 /world）"]
        C["聊天台 /chat<br/>web/app.js + avatar/index.js"]
        I["实时传译页<br/>web/interpret.js"]
    end

    subgraph "FastAPI 进程（server/app.py，同源部署）"
        WS["/ws 主语音通道<br/>（accept 前有同源校验）"]
        WSI["/ws/interpret · /ws/interpret-stream<br/>（传译，无同源校验 → 见缺口）"]
        WSP["/ws/proactive<br/>（服务端在册，前端未见连接）"]
        REST["REST：/api/persona · /api/avatar · /api/memories*<br/>/api/executive/*（executive_api 路由）· /api/eval/scenarios · /health"]
        subgraph "快环 System 1（server/dialogue/）"
            ORCH["orchestrator<br/>stream_respond · respond · ambient_event<br/>executive_route（意图分流）"]
            DELIV["delivery.deliver_reply<br/>（narrate/主动/助手回执共用语音出口）"]
            EXP["experience · practice<br/>（体验/练习两模式）"]
        end
        subgraph "慢环 System 2（server/assistant/）"
            EXEC["executive<br/>delegate · drive · run_commission · accept · reject"]
            DISP["dispatcher<br/>open · dispatch · retry_with_feedback · _run_adapter"]
            ADP["adapters/ 注册表<br/>cli_subproc · durable · http_api · manual · mcp"]
            EVAL["evaluator<br/>L1 客观校验 + L2 审美判例 · distill()"]
            TELE["telemetry<br/>span 落库 · 鬼打墙熔断"]
        end
        subgraph "记忆层（server/memory/）"
            RETR["retriever<br/>search · build_context · deliver_context"]
            PRES["presentation.present_memory<br/>stated/inferred/uncertain 三档"]
            MGR["manager.observe_turn<br/>唯一写入出口（自动抽取 + 手动）"]
            TEMP["temporal<br/>valid-time 保守解析"]
        end
        BC["broadcast/v2<br/>CommissionEventBridge · v2_hub"]
    end

    subgraph 持久层
        DB[("SQLite：memories（含 valid_from/until）<br/>· messages · diary · commissions（threads.py）<br/>· telemetry spans")]
    end

    subgraph 外部引擎
        ASR["ASR（speech/asr.py · step 流式）"]
        LLM["LLM（server/llm.py<br/>stream · complete · Mock 兜底）"]
        TTS["TTS（speech/tts.py<br/>synthesize · 微句切分 · 情绪音色）"]
        AG["执行体 Agent 进程<br/>claude / workbuddy / dsh / sandbox / durable…"]
    end

    W -- "GET / · /world 静态" --> REST
    W -- "REST 全量 + WS 增量 commission_update" --> WS
    C <--> WS
    I <--> WSI
    WS -- "_run_turn：音频先过 ASR" --> ASR
    WS <--> ORCH
    WS <--> EXP
    ORCH -- "每轮上下文（三档置信度装配）" --> RETR
    RETR --> PRES
    ORCH -- "llm.stream" --> LLM
    ORCH -- "微句 synthesize" --> TTS
    ORCH -- "轮末 observe_turn" --> MGR
    MGR --> TEMP
    MGR --> DB
    RETR --> DB
    ORCH -- "委托意图命中 → conn.spawn 卸载" --> EXEC
    EXEC --> DISP --> ADP --> AG
    EXEC --> EVAL
    EXEC --> TELE
    EXEC -- "CommissionStore.create/transition" --> DB
    DB -- "台账回调（跨线程）" --> BC
    EXEC -. "状态变更" .-> BC
    BC -- "v1 commission_update 广播 + v2 六类事件" --> WS
    DELIV --> TTS
    DELIV --> WS
```

要点（均为已核验设计约束）：快环内**零阻塞调用**，重活经 `conn.spawn` 异步卸载到慢环；
新增 Worker 只改 `config/agents_roster.yaml` 与适配器，调度核心零改动；
无 API Key 时 `LLM._mock` 自动兜底，全链路可跑。

---

## 图 2 · 快环一轮语音往返（System 1 数据流）

```mermaid
sequenceDiagram
    participant B as 浏览器（/chat 或世界页）
    participant W as /ws（app.py _run_turn）
    participant A as ASR
    participant O as orchestrator.stream_respond
    participant M as retriever.build_context
    participant L as llm.stream
    participant T as tts.synthesize

    B->>W: 音频帧（conn.recording 收流）
    W->>A: _transcribe_turn → 识别文本
    A-->>W: text（asr 事件回显）
    W->>O: session_id + user_text
    Note over O: executive_route 先分流：委托/进度/战况/验收<br/>命中委托 → conn.spawn 卸载慢环，本图不再展开
    O->>M: 三档置信度装配记忆块<br/>（检索为空 → 显式注入负向约束，禁脑补）
    M-->>O: 上下文块 + 活动线程摘要
    O->>L: 系统 Prompt（persona.bias_block + AQI_BLOCK_INSTRUCTION）
    L-->>O: token 流
    Note over O: AqiBlockFilter 字符级截留<br/>[情绪]标签驱动音色；<aqi-block> 绝不进 TTS
    O-->>W: thinking / text_delta / aqi_blocks（聚合一条，先于 done）
    O->>T: 微句切分后逐段合成（预加载队列，超时跳过）
    T-->>O: SpeechChunk 音频
    O-->>W: audio 事件（base64 过 WS 边界）
    W-->>B: turn 帧序列（audio.chunk 携 index/seq）
    O--)M: 轮末 manager.observe_turn → 抽取写库（MEMORY_UPDATE v2）
    Note over W: done/error → conn.finish；barge_in → 取消在飞合成，interrupted 后零迟到音频
```

主动开口支线：`ambient_event`（空闲退避 + 冷启动破冰）与 app.js 45s 心跳驱动的
`proactive_check` 走同一 `deliver_reply` 语音出口，复用打断与轮次围栏。

---

## 图 3 · 慢环一单委托（System 2 数据流）

```mermaid
sequenceDiagram
    participant K as 快环（executive_route 命中）
    participant X as executive
    participant S as CommissionStore(threads.py)
    participant D as dispatcher
    participant AD as adapter（注册表按 roster 绑定）
    participant AG as 执行体
    participant TE as telemetry
    participant EV as evaluator
    participant BB as broadcast(v2_hub + 台账回调)
    participant W as 在线 WS 连接 / 像素世界

    K->>X: conn.spawn → delegate(brief)
    X->>S: create（阶段机入队，乐观并发 RevisionConflict 兜底）
    X->>D: dispatch（adapter_for 选将，核心无 if-agent-type 分支）
    D->>AD: _run_adapter
    AD->>AG: cli_subproc 子进程 / durable / http_api / manual / mcp
    AG-->>TE: span 上报（鬼打墙熔断盯循环）
    AG-->>AD: 交付物 artifact
    X->>EV: L1 客观校验（字段/文件/接线/预算）→ L2 审美判例
    alt 通过
        X->>S: transition → 待验收（accept/reject 入口）
    else 打回（上限 MAX_RETRY）
        X->>D: retry_with_feedback 再派
    end
    S--)BB: 台账变更回调（任意线程，非就地 create_task）
    BB-->>W: v1 commission_update（载荷=GET /api/executive/commissions 的 items 元素）
    BB-->>W: v2 六类委托事件（CommissionEventBridge diff 投影，与 v1 并存，加法不替换）
    Note over K,X: 台前语音可驱动到终态：委托 → drive → run_commission；<br/>多单未验收时前台强制澄清选择题，不收工/打回
```

---

## 图 4 · 记忆层读写环

```mermaid
graph LR
    subgraph 写路径
        TURN["对话轮结束<br/>orchestrator → manager.observe_turn"] --> EX["extractor.extract_memories<br/>（LLM 抽取，evidence 引文约束）"]
        MANUAL["/api/memories POST · remember/correct<br/>forget-key · toggle · reset"]
        EX --> VT["temporal：valid_from/until<br/>宁可漏设不可错设"]
        VT --> DB[("SQLite memories<br/>source=stated/inferred/uncertain")]
        MANUAL --> DB
    end
    subgraph 读路径
        DB --> RR["store.retrieval_ranking_rows<br/>（两阶段：先 ranking 行再 marshal）"]
        RR --> RS["retriever.search / build_context<br/>TF-IDF+向量 + recency + utility<br/>过期事实 ×validity_expired_factor 沉底不丢"]
        RS --> PM["presentation.present_memory<br/>三档置信度 → 措辞与来源前缀"]
        PM --> CTX["快环上下文（每轮注入）"]
        PM --> API["/api/memories* REST<br/>（to_dict 不含 valid-time 字段 → 契约形状不变）"]
    end
    DEL["forget：物理硬删除<br/>不留索引"] -.-> DB
```

设计红线在图上的落点：三档置信度装配、空检索负向约束在图 2；硬删除在本图 `DEL`；
快环零阻塞在图 2 的 `conn.spawn` 卸载点。

---

## 图 5 · 广播与像素世界投影

```mermaid
graph TB
    CS["CommissionStore 台账"] -- "变更回调（跨线程）" --> CB["_broadcast_commission<br/>v1：payload.commission 与 REST items 同形"]
    CS --> BR["CommissionEventBridge<br/>diff → 六类 v2 事件"]
    BR --> HUB["v2_hub（每连接 sink，/ws attach 时注册）"]
    CB --> CONN["全部在线 /ws 连接<br/>（无在线连接静默丢弃：状态以 REST 为准）"]
    HUB --> CONN
    LEDGER["pixel-world ledger.ts"] -- "首连 REST 全量<br/>GET /api/executive/commissions · /roster · /adapters" --> CS
    LEDGER -- "WS 增量 commission_update<br/>（连的就是主 /ws）" --> CONN
    LEDGER -- "断线指数退避重连 + 定时 reconcile 兜底" --> CS
    WP["GET / → world_entry_response<br/>mount_pixel_world → /world 静态"] --> LEDGER
```

---

## 证据锚（关键边 → 可核对的符号）

| 边 | 锚（2026-10-29 核对，符号名为准） |
|---|---|
| /ws accept 前同源校验，拒绝→HTTP 403 | `server/app.py` 主 websocket handler 头部（netloc 比对） |
| /ws/proactive、/ws/interpret* 无同源校验（缺口 B-1/B-3） | `server/speech/interpretation.py`、`stream_interpretation.py` 内 `origin` 0 次 |
| 委托意图 spawn 卸载 | `app.py` `_run_turn` 内 `conn.spawn(_executive_delegate(...))`；`orchestrator.executive_route` |
| 快环一轮链 | `orchestrator.stream_respond`（build_context / llm.stream / tts.synthesize / AqiBlockFilter 各调用点） |
| 轮末抽取唯一出口 | `manager.observe_turn` → `extract_memories`；MEMORY_UPDATE 发在该出口（`manager.py` §5.6 注释） |
| CommissionStore 6 阶段 + 乐观并发 | `interaction/threads.py` `CommissionStore.create/transition`、`RevisionConflict` |
| 核心无 agent-type 分支 | `dispatcher.adapter_for` 经注册表取适配器（V3 有测试背书，见 TASKS V3 段） |
| v1 广播与 REST 同形 | `app.py::_broadcast_commission` docstring 与实现 |
| v2 并存是加法 | `app.py` §25 注释「与 v1 commission_update 并存」；`broadcast/v2.py::CommissionEventBridge` |
| 世界投影只读台账 | `pixel-world/src/world/ledger.ts`（REST 全量 + WS 增量 + reconcile） |
| 主页=世界，旧聊天页 /chat | `app.py` `index()` docstring、`mount_pixel_world` |

### 核对命令（重跑即得现态，不依赖本文）

```bash
# WS 端点全集与 origin 覆盖
Select-String -Path server/app.py -Pattern '@app\.websocket' -Context 0,18 | Select-String 'origin'
# 快环链符号
Select-String -Path server/dialogue/orchestrator.py -Pattern 'def (stream_respond|respond|ambient_event|executive_route)|build_context|llm\.stream|tts\.synthesize'
# 慢环与广播
Select-String -Path server/assistant/executive.py,server/assistant/dispatcher.py,server/broadcast/v2.py -Pattern 'def (delegate|drive|run_commission|accept|reject|dispatch|adapter_for)|class CommissionEventBridge|v2_hub'
```

`python scripts/check_protocol.py` 本身即为「前后端 /api 与 WS 消息形状一致」的常驻门禁，
本图不复制其结论。

---

## 已知边界（图上如实标注，不粉饰）

1. **三个次要 WS 缺同源校验**（B-1/B-3，P2）：改 `server/**` 需单独批准，见 `docs/NEXT_PHASE_PLAN.md` §10.1。
2. `/ws/proactive` 服务端在册但前端未见连接（主 /ws 心跳已承担该职责，R0-2 落地）；端点去留属 Role 0。
3. 图中「durable」适配器在册、`enabled: false` 的 codex 不进选将池 —— 花名册现状以
   `Select-String config/agents_roster.yaml 'adapter:'` 实跑为准（成员数不写死，理由见 PI_DURABLE 校正块）。
