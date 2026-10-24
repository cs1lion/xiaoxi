# [PROTOCOL_REQUEST] 第 8 步：事件协议 v2（9 类新 WS 事件）

**日期**：2026-10-23
**依据**：`docs/AGENT_PLAYBOOK.md:93-102`（开工前协议红线确认）
**规范来源**：`docs/PIXEL_WORKSHOP_SPEC_V2.md` §5（`:772-846`，标注「状态：提案，未生效」）

---

## 1. 结论：契约**不够用**

现有 `docs/PROTOCOL.md` 对第 8 步**完全装不下**。实测核对：

| 事件 | `PROTOCOL.md` 命中数 | 代码里是否已发 |
|---|---|---|
| `TASK_STATE_CHANGE` | **0** | 否（`machine.py` 只作为迁移表的「该发什么」标注） |
| `STEP_STATE_CHANGE` | **0** | 否（同上） |
| `CONTRACT_PROPOSED` | **0** | 否（同上） |
| `ASK_USER` | **0** | 否（同上） |
| `TOOL_EXECUTION` | **0** | 否（同上） |
| `VERIFICATION_REPORT` | **0** | 否（`verify/result.py` 已按载荷形状建好，但未上 WS） |
| `DELIVERY_EFFECT` | **0** | 否（`delivery.py:17` 明写「不发事件，上 WS 属第 8 步」） |
| `MEMORY_UPDATE` | **0** | 否（零实现） |
| `AGENT_PRESENCE` | **0** | 否（零实现） |

**9 类全部是新增**。今天的 WS 事件面只有 `commission_update` 等既有类型。

---

## 2. 需要新增的东西（逐项）

### 2.1 信封（§5.1）

```ts
interface EventEnvelope<T extends EventType, P> {
  v: 2;              // 信封版本，与 v1 并存
  id: string;        // 全局唯一，消费方按 id 去重（至少一次投递）
  seq: number;       // 连接级单调递增，断线后按它补齐
  ts: number;        // 服务端时钟，epoch ms
  type: T;
  task_id?: string;  // 与任务无关的事件（如 AGENT_PRESENCE）可省略
  step_id?: string;  // 仅 STEP_* 事件有
  session_id?: string;
  turn_id?: string;
  payload: P;
}
```

**三个需要 Role 0 确认的取舍**：

1. `seq` 是**连接级**单调，不是全局单调。多进程写同一 SQLite 时全局单调要加锁或引入单写者，单用户本地系统不值得。断线补齐靠「`seq` + `after_seq` 查询」。
2. `ts` 用**服务端**时钟。前端时钟不可信（用户可改系统时间）。
3. `v` 是**信封版本**，不是 schema 版本。演进靠「未知字段忽略、未知 type 整条忽略」。

### 2.2 九类事件的 payload

| type | 谁发 | payload | 前端表现 | 投递保障 |
|---|---|---|---|---|
| `TASK_STATE_CHANGE` | TaskEngine | `from,to,reason?,resume_to?,attempt,risk` | Task 级动画 | 至少一次 |
| `STEP_STATE_CHANGE` | TaskEngine | `from,to,agent,station?,attempt` | 小人走位 | 至少一次 |
| `CONTRACT_PROPOSED` | IntentGate | `contract_id,version,goal,artifact,inputs_kind[],deliver_to_shown?,risk,highlighted_slots[],criteria[],budget` | 委托卷轴 | 至少一次 |
| `ASK_USER` | TaskEngine/Gate | `question,options[],missing[],deadline?` | 感叹号 + 选项胶囊 | 至少一次 |
| `TOOL_EXECUTION` | Worker 适配器 | `tool,status,steps_done?,steps_total?,detail?` | 闪烁/进度条 | 至多一次（可丢） |
| `VERIFICATION_REPORT` | Verifier | `passed,findings[],criteria[{id,kind,passed,evidence?}]` | 质检台 | 至少一次 |
| `DELIVERY_EFFECT` | 交付执行器 | `effect_id,type,target_class,status,idempotency_key` | 宝箱 | 至少一次 |
| `MEMORY_UPDATE` | MemoryService | `action,memory_ref,source,confidence,provenance` | 档案室 | 至少一次 |
| `AGENT_PRESENCE` | 会话层 | `state:idle\|listening\|thinking\|speaking` | 麦克风/气泡 | 至多一次（可丢） |

### 2.3 前端 → 后端命令（§5.3）

```ts
type Command =
  | { type: 'CONFIRM_CONTRACT'; task_id; contract_id; version; confirmed_slots[]; request_id }
  | { type: 'ANSWER'; task_id; answer; option_index?; request_id }
  | { type: 'CANCEL_TASK'; task_id; reason?; request_id }
  | { type: 'APPROVE_SIDE_EFFECT'; task_id; effect_id; request_id }
  | { type: 'RETRY_STEP'; task_id; step_id; request_id }
```

**这 5 个命令是否也属本次范围，需要 Role 0 定**——它们与事件成对，只发事件不接命令，前端按钮点了没反应。

### 2.4 明确**不加**的东西

- **无 `TASK_SNAPSHOT` 事件**：首帧对齐走 REST 全量，再发快照就是造第二个真相源。
- **无 `progress(0-100)`**：绝大多数工具给不出真实百分比，假进度条破坏「画面等于真实状态」。改成 `steps_done/steps_total` 或 `indeterminate: true`。
- **v0.1 的 `VOICE_STATE` 改名 `AGENT_PRESENCE`**：世界前端不一定播语音，绑死在语音状态上会逼出第二个事件。

---

## 3. 同时必须改的东西（否则门形同虚设）

### 3.1 `scripts/check_protocol.py` 有单向漏洞

实测：它只拦「前端读了、后端没发」那一半。**反方向（后端发了、前端没接）只打印一行，不报错。**

规范 §5 开头要求的落地顺序是：

> 协议评审 → 加事件 → **同时改 `docs/PROTOCOL.md` 与 checker** → 再接前端

所以第 8 步落地时必须顺手补上 checker 的反向拦截，否则以后任何后端新增事件都能静默通过。

### 3.2 `docs/PROTOCOL.md` 要同步

9 类事件 + 信封 + 5 个命令都要写进协议文档。这是 Role 0 的文件（`AGENT_PLAYBOOK.md:4` 写明「只有 Role 0 可改 `docs/PROTOCOL.md`」）。

---

## 4. 我需要 Role 0 决定的三件事

1. **这 9 类事件的名字和字段，认不认。**
2. **5 个前端命令是否同批做**（不做则前端按钮无响应）。
3. **`check_protocol.py` 的反向拦截由谁写**——我写实现 + 测试，还是 Role 0 亲自改 checker。

---

## 5. 停工声明

按 `AGENT_PLAYBOOK.md:101`：「输出 `[PROTOCOL_REQUEST]` 并停工等 Role 0。Role 0 未确认前不动业务代码。」

**我现在停工。** 未确认前不写任何 WS 事件代码、不改 `docs/PROTOCOL.md`、不动 `check_protocol.py`、不改前端。

已就位的、不越红线的东西保持不动：
- `server/contract/parser.py`（第 1 步解析层）
- `server/contract/bridge.py`（接线封装，未接）
- 全部测试

第 9 步已由用户定 A（不接），与本次请求无关。
