# 小汐 × 像素工坊：契约、状态机与事件协议 v2 规范

草案 v0.2 · 2026-10-21 · 对应《小汐双系统语音 Agent × 像素工坊 技术设计文档》审阅意见（P0 与 P1 部分）

**v1.0 采纳版 · 2026-10-22**：用户裁定本文档为其本人所写，纳入仓库，作为 M1/M2 的直接输入。
冲突裁决（12 处）与实施顺序见 `docs/V2_SPEC_INTEGRATION.md`；本文档的技术结论未改。

- v0.1（同日）：§0–§3.7
- v0.2（同日）：补齐 §3.8 与 §4–§9，加附录 A–D（三个追问的正面回答 / 参考报告核查 / 沙盘截图审阅 / 初版设计回顾）
- 本版**没有**改动 v0.1 的任何原文；新增内容一律追加，改动建议单列成小节并标出冲突项

## 0 文档定位与阅读方式

**目的**：把审阅意见落成可实现、可测试的规范，作为 M1（后端契约与验收闭环）和 M2（事件桥与最小像素工坊）的直接输入。

**范围**：TaskContract 与意图完备度拦截器、Task 与 Step 双层状态机、Verifier、交付与记忆晋升、事件协议 v2、权限与沙箱、可观测性、评测集、里程碑验收证据。

**不在范围**：像素美术、地图与光影、Agent 内核的提示词和模型选择。

**约定**：“必须”表示强制；“应当”表示默认遵守，有充分理由可偏离；“可以”表示可选。文中所有阈值和上限都是配置项，数值只是初始默认值，需要用第 9 节的评测集校准。

**阅读顺序**：实现 M1 先读第 2、3、3.8、4、6、7、8、9 节；实现 M2 先读第 5、6 节。
（v0.2 补上 3.8：§3.7 已实现，但 §6 落地后有三处需要升级。）

**与原文档的关系**：保留四个角色——意图完备度拦截器、任务状态机、独立 Verifier、事件总线，但不绑定“快慢双系统”的具体实现，只通过 2.2 节的四个接口对接。内核实现换了，本规范不用改。

## 1 术语表

| 术语 | 定义 |
| --- | --- |
| Task 任务 | 一次已被用户确认并锁定契约的工作单元，有唯一 task\_id |
| Step 步骤 | Task 内 DAG 的一个节点，由一个 Worker 执行 |
| Contract 契约 | 冻结后的结构化任务单：目标、输入、交付物、验收标准、风险、副作用、预算 |
| Slot 槽位 | 契约中的一个字段值，附带来源和确认状态 |
| Criterion 验收标准 | 契约中的一条可判定条件，带类型和检查方式 |
| Worker | 执行 Step 的子智能体 |
| Verifier | 独立于 Worker 的验收者，含确定性检查器和语义 Judge |
| Station 工位 | 后端给出的语义位置（如 code\_terminal），不含任何坐标 |
| Event 事件 | 后端到前端的状态投影，带全局递增的 seq |
| Command 命令 | 前端到后端的用户操作 |
| Snapshot 快照 | 某个 seq 时刻的完整可见状态，用于重连恢复 |
| Block 阻塞 | 任务暂停等待人工决策或授权，带 reason 和 resume\_to |
| Attempt 尝试 | Task 进入 EXECUTING 的第 n 次，验收打回后递增 |
| Signature 失败签名 | 失败的归一化指纹，用来识别“同样的错误又发生了” |

## 2 设计原则与接口边界

### 2.1 十条原则

| # | 原则 | 如何检查 |
| --- | --- | --- |
| P1 | 契约先于执行：未到 SPEC\_LOCKED 的任务不得派单 | 状态机单测：非法迁移被拒 |
| P2 | 验收标准必须可验：每条标准有类型和检查方式 | 拦截器 lint（3.7） |
| P3 | 执行者不自评：Worker 的“已完成”只是信号，不是判定依据 | Verifier 输入中不含 Worker 自述（4.8） |
| P4 | 副作用后置：不可逆动作只在 ACCEPTED 之后由确定性代码执行 | 评测集 C 类：高风险任务未经确认执行数为 0 |
| P5 | 状态只沿迁移表变化，其余一律拒绝并记日志 | 迁移表驱动的属性测试 |
| P6 | 事件是状态的投影，真相只在后端 | 断线重连后前端状态与快照一致 |
| P7 | 前端无 Agent 逻辑，后端无坐标和动画 | 评审：后端不出现 x/y，前端不出现任务判定 |
| P8 | 记忆写入需要证据与限幅，且可回滚 | 每次变更带 provenance，有回滚用例 |
| P9 | 一切可回放：事件流追加写入，同时是 trace 和评测数据 | 同一份 JSONL 能重放出相同的最终状态 |
| P10 | 失败要有名字：每种失败有类型、签名和下一步 | 失败分类表（4.6）覆盖所有失败路径 |

### 2.2 四个接口

```ts
interface IntentGate {
  evaluate(draft: ContractDraft, mem: MemoryView): GateResult;
}
type GateResult =
  | { status: 'ready'; contract: TaskContract }
  | { status: 'needs_clarification'; ask: AskUser; missing: string[] }
  | { status: 'rejected'; reason: 'unverifiable_criteria' | 'unsafe' | 'out_of_scope' };

interface TaskEngine {
  submit(contract: TaskContract): TaskId;
  handle(cmd: Command): CommandResult;   // 唯一的写入口
  view(id: TaskId): TaskView;
}

interface Verifier {
  verify(req: VerifyRequest): Promise<VerificationReport>;
}

interface EventBus {
  publish(e: DomainEvent): void;          // 由总线补 id、seq、ts
  subscribe(fromSeq: number, sink: EventSink): Unsubscribe;
}
```

### 2.3 模块关系

```
用户（语音或文字）
   │ 话语
   ▼
[IntentGate] ◄── [MemoryView] ◄── [MemoryService] ◄── 晋升与降级（§5.6）
   │ ready
   ▼
[TaskEngine] ──派单──► [Worker 池] ──产出物──┐
   │  ▲                                       ▼
   │  └────── 打回 / 通过 ◄────────────── [Verifier]
   │
   └──所有状态变化──► [EventBus] ══ WebSocket ══► 像素前端
                          ▲                          │
                          └──────── 命令（Command）◄──┘
```

## 3 TaskContract 与意图完备度拦截器

### 3.1 数据结构

```ts
type RiskLevel = 'low' | 'medium' | 'high';

type SlotSource =
  | { kind: 'user_utterance'; turn_id: string }
  | { kind: 'memory'; memory_id: string; confidence: number }
  | { kind: 'default'; reason: string }
  | { kind: 'user_confirmed'; turn_id: string };

interface Slot<T> {
  value: T;
  source: SlotSource;
  confirmed: boolean;        // 用户是否在契约卡上确认过
  highlight?: boolean;       // 契约卡上是否需要高亮提示
}

interface InputRef {
  type: 'file' | 'dir' | 'url' | 'text' | 'memory_ref';
  uri: string;               // 路径或 URL；敏感内容用 secret_ref，不放明文
  resolved_by?: string;      // 例如 modified_yesterday
  exists?: boolean;          // 拦截器用 stat 或探测校验后填写
}

interface ArtifactSpec {
  kind: 'file' | 'report_md' | 'csv' | 'code_patch' | 'message' | 'chart' | 'other';
  format?: string;
  location?: string;         // 工作区内的相对路径
}

interface Budget {
  max_retries: number;       // 默认 3：验收打回次数
  max_step_retries: number;  // 默认 2：Step 内部工具重试
  max_wall_s: number;
  max_tokens?: number;
  max_cost_usd?: number;
  max_tool_calls?: number;
}

interface SideEffect {
  id: string;
  type: 'send_message' | 'http_write' | 'overwrite_file' | 'write_outside_workspace'
      | 'delete' | 'spend_money' | 'install' | 'other';
  target: string;            // 目标描述或 secret_ref
  target_class: 'self' | 'third_party' | 'local';
  reversible: boolean;
  has_backup?: boolean;
  idempotency_key: string;   // 交付阶段防重复执行
}

interface RiskAssessment { level: RiskLevel; reasons: string[]; }

interface Criterion {
  id: string;                // C1、C2……
  text: string;              // 人话描述，用于契约卡展示
  kind: 'deterministic' | 'semantic' | 'human';
  check: CheckSpec;
  required: boolean;         // 默认 true；false 为建议项，不影响 ACCEPTED
  promotion_weight: number;  // 记忆晋升权重：deterministic 1.0、semantic 0.4、human 1.0
}

type CsvRule = { name: string; args?: Record<string, unknown> };  // 内置规则库，见 3.7

type CheckSpec =
  | { type: 'file_exists_nonempty'; path: string; min_bytes?: number }
  | { type: 'schema_valid'; path: string; format: 'json' | 'csv'; schema_ref: string }
  | { type: 'syntax_ok'; path: string; lang: 'python' | 'ts' | 'json' | 'yaml' }
  | { type: 'exit_code_zero'; cmd: string; cwd: string; timeout_s: number }
  | { type: 'csv_assert'; path: string; rules: CsvRule[] }
  | { type: 'rubric'; rubric: string[]; evidence_required: true }          // semantic
  | { type: 'ask_user'; question: string; options: string[] };              // human

interface TaskContract {
  contract_id: string;
  version: number;           // 任何改动递增；SPEC_LOCKED 后冻结
  created_at: string;
  utterance: string;         // 用户原话
  goal: Slot<string>;
  inputs: Slot<InputRef>[];
  artifact: Slot<ArtifactSpec>;
  deliver_to?: Slot<string>;
  acceptance: Criterion[];
  risk: RiskAssessment;
  side_effects: SideEffect[];
  permissions_required: string[];   // 第 7 节的权限等级，如 L1、L4
  budget: Budget;
}
```

### 3.2 字段约束

| 字段 | 约束 | 来源 | 校验时机 |
| --- | --- | --- | --- |
| goal | 一句话，形如“动词 + 对象”，不超过 80 字 | 用户话语或补全 | G1 |
| inputs | 每项必须可解析：文件存在、URL 可达或明确为 text；无输入的任务才允许为空 | 记忆补全优先，其次追问 | G2（stat 或探测） |
| artifact | 必须指明 kind；file 类必须给 format | 用户、记忆或默认 | G3 |
| acceptance | 至少 1 条 required；全部通过 lint（3.7） | Gate 生成草案，用户可改 | G4 |
| risk | 由规则（3.4）计算，不允许手填降级 | 规则引擎 | G5 |
| side\_effects | 必须穷举；运行中出现未声明的副作用即违规，Task 进入 FAILED | 契约生成时推断，用户确认 | G5 与运行时审计 |
| budget、permissions\_required | 有默认值，可按任务类型覆盖 | 配置 | G6 |
| version | SPEC\_LOCKED 后只读；任何改动必须回到 PROPOSED 并递增 | TaskEngine | 迁移守卫 |

### 3.3 意图完备度拦截器（IntentGate）

**流程**

1. 解析话语，生成 ContractDraft（goal、inputs、artifact、deliver\_to 候选）。
2. 记忆槽位补全（3.5）。
3. 运行检查 G1 到 G6。
4. 全部通过返回 ready；否则选出“影响最大的一个缺口”发起追问。

**检查项**

| 编号 | 检查 | 通过条件 |
| --- | --- | --- |
| G1 | 目标清晰 | goal 能写成“动词 + 对象”，且只有一个主目标 |
| G2 | 输入可解析 | 每个 input 的 exists 为 true，或类型为 text |
| G3 | 交付物明确 | artifact.kind 已确定，file 类有 format |
| G4 | 验收标准可验 | 至少 1 条 required 标准，且全部通过 3.7 的 lint |
| G5 | 风险与副作用已评估 | side\_effects 穷举完毕，risk 由 3.4 规则算出 |
| G6 | 预算与权限已声明 | budget、permissions\_required 非空，权限在 7.1 的可授予范围内 |

**追问策略**

- 每轮只问 1 个问题。按影响排序：副作用槽位 > 输入源 > 交付物 > 验收标准 > 其他。
- 每个问题附 2 到 3 个选项，其中 1 个标为默认；同时允许自由回答。
- 追问总轮数上限 3。超限后：用最保守的默认值填充，并将这些槽位标 highlight，进入 PROPOSED；此类任务不得走倒计时自动开工，必须显式确认。
- 用户回答“随便”“你定”时，按默认值处理，source 记为 default。
- 返回 rejected 的情形：无法生成任何可验的标准，或请求超出可授予权限。此时小汐向用户说明原因，不进入状态机。

### 3.4 风险规则与确认策略

**风险由规则算出，取所有副作用中的最高值：**

| 副作用 | 条件 | 风险 |
| --- | --- | --- |
| 只读，或只写工作区内 | 无 | low |
| overwrite\_file | 有自动备份 | medium |
| overwrite\_file | 无备份 | high |
| write\_outside\_workspace | 仅新建文件 | medium |
| send\_message、http\_write | target\_class 为 self | medium |
| send\_message、http\_write | target\_class 为 third\_party | high |
| delete、spend\_money、install | 无 | high |

另有一条兜底：任何 reversible 为 false 的副作用，风险至少为 medium。

**按风险的确认策略：**

| 风险 | 开工确认 | 契约卡倒计时 | 交付前确认 |
| --- | --- | --- | --- |
| low | 可自动开工 | 允许，默认 3 秒（语音场景建议 5 秒）；仅当契约卡上没有高亮槽位时 | 不需要 |
| medium | 必须显式确认 | 不允许 | 需要，展示将执行的动作与预览 |
| high | 必须显式确认，并对高亮的关键槽位逐项点头 | 不允许 | 必须，交付时再次展示目的地与内容摘要 |

“显式确认”指前端发出 CONFIRM\_CONTRACT 命令；语音确认（如说“开工”）也必须经 ASR 转成同一条命令，并且与当前展示的 contract\_id 和 version 绑定，防止确认了旧契约。

### 3.5 置信度使用规则

| 槽位类别 | 置信度 c | 行为 |
| --- | --- | --- |
| 非副作用槽位 | c ≥ 0.85 | 静默补全，来源折叠展示在契约卡里 |
| 非副作用槽位 | 0.70 ≤ c < 0.85 | 补全，并在契约卡上高亮“请确认” |
| 非副作用槽位 | c < 0.70 | 不补全，转为追问 |
| 副作用槽位（目的地、收件人、被覆盖对象） | 任意 | 必须在契约卡显式展示并高亮；risk 为 medium 及以上时必须点头 |
| 多条记忆冲突 | 任意 | 取最近一次 user\_confirmed 的；没有则追问 |

```yaml
memory:
  silent_fill_min: 0.85
  confirm_fill_min: 0.70
  side_effect_slots_always_show: true
```

**校准**：用评测集 A 类样本画“补全覆盖率 - 补全准确率”曲线选阈值。静默补全档的准确率目标不低于 95%，因为这一档用户看不到。阈值不是常量，不要写死在代码里。

### 3.6 完整示例：“小汐，把昨天那个竞品表格更新一下发我”

```yaml
contract_id: ct_demo_001
version: 1
utterance: 小汐，把昨天那个竞品表格更新一下发我
goal:
  value: 更新竞品对比表的最新数据，并把更新后的表格发给用户
  source: { kind: user_utterance, turn_id: t_88 }
  confirmed: false
inputs:
  - value: { type: file, uri: ~/work/competitors/竞品对比.csv, resolved_by: modified_yesterday, exists: true }
    source: { kind: memory, memory_id: m_0142, confidence: 0.91 }
    confirmed: false
artifact:
  value: { kind: csv, format: csv, location: out/竞品对比.csv }
  source: { kind: memory, memory_id: m_0142, confidence: 0.91 }
  confirmed: false
deliver_to:
  value: secret_ref:webhook/self_notify
  source: { kind: memory, memory_id: m_0177, confidence: 0.88 }
  confirmed: false
  highlight: true             # 副作用槽位，必须展示
side_effects:
  - id: se_1
    type: send_message
    target: secret_ref:webhook/self_notify
    target_class: self
    reversible: false
    idempotency_key: ct_demo_001:se_1
acceptance:
  - id: C1
    text: 输出文件存在且不为空
    kind: deterministic
    check: { type: file_exists_nonempty, path: out/竞品对比.csv, min_bytes: 100 }
    required: true
  - id: C2
    text: 表头与原表一致，行数不少于原表
    kind: deterministic
    check: { type: csv_assert, path: out/竞品对比.csv, rules: [{ name: header_equals_source }, { name: row_count_gte_source }] }
    required: true
  - id: C3
    text: 被更新的行，“更新时间”列为今天
    kind: deterministic
    check: { type: csv_assert, path: out/竞品对比.csv, rules: [{ name: changed_rows_column_is_today, args: { column: 更新时间 } }] }
    required: true
  - id: C4
    text: 变更摘要逐条列出新增与修改的竞品，每条附来源链接
    kind: semantic
    check:
      type: rubric
      rubric:
        - 摘要里的每个条目都能在输出表中找到对应行
        - 每个条目都附有来源链接，且该链接出现在 Worker 的抓取记录中
      evidence_required: true
    required: true
risk: { level: medium, reasons: [向用户自己的 Webhook 发送内容，不可撤回] }
permissions_required: [L1, L4, L5]
budget: { max_retries: 3, max_step_retries: 2, max_wall_s: 900 }
```

这张契约卡里，deliver\_to 因为是副作用槽位，即使置信度 0.88 也要高亮展示；risk 为 medium，所以不走倒计时，必须用户点头。

### 3.7 验收标准的编写规范与 lint

| 编号 | 规则 |
| --- | --- |
| L1 | 每条标准必须有 kind，并且 check 的 type 与 kind 匹配 |
| L2 | deterministic 必须带可执行的 check；其中所有路径和命令必须能在契约范围内解析 |
| L3 | semantic 必须带 rubric，至少 1 条，每条是可判真假的陈述，并声明需要引用的证据；“内容准确”“质量高”这类无证据陈述一律拒绝 |
| L4 | 至少 1 条 required 的 deterministic 标准；纯语义任务（如写作）例外，但必须有至少 1 条 human 标准 |
| L5 | 标准不得引用 Worker 的自述，如“Worker 报告已完成” |
| L6 | required 标准不超过 8 条，避免验收成本失控 |
| L7 | semantic 标准的数量不得超过 deterministic 的两倍，防止验收退化成全靠模型感觉 |

**好坏示例**

| 差 | 好 |
| --- | --- |
| 表格更新正确 | C2 表头一致、行数不少于原表；C3 被改行的更新时间为今天 |
| 报告质量高 | rubric：每个结论都引用了数据来源；报告覆盖了契约里列出的全部 5 个竞品 |
| 代码能用 | exit\_code\_zero：运行 pytest，退出码为 0；syntax\_ok：新增文件通过语法检查 |
| 发送成功 | 不属于验收，属于交付（§4.4 T20 / §5.2 `DELIVERY_EFFECT`）的 effect\_log |

**内置 CsvRule 规则库（M1 最小集）**：header\_equals\_source、row\_count\_gte\_source、no\_empty\_cells（可指定列）、column\_values\_in\_set、changed\_rows\_column\_is\_today、unique\_key（可指定列）。规则库可以扩展，但每条规则必须是纯函数、无网络、可在 1 秒内跑完。

---

## 3.8 §3.7 的升级项与落地状态（原「三处修订建议」）

§3.7 的 lint 已经实现并有测试背书（`scripts/test_contract.py`），所以**不重写**它，只做三处升级。

**2026-10-22 落地状态**：

| # | 状态 | 落点 / 依据 |
| --- | --- | --- |
| R1 | **已实现** | `config/contract.yaml` 的 `lint.allowed_commands` + `lint.py:_cmd_allowed`，黑名单保留作第二道（`9ffc099`） |
| R2 | **已实现** | `SEMANTIC_ONLY_ARTIFACT_KINDS = {report_md, message, chart}`（`adae2ba`） |
| R3 | **未实现** | 语义已由本节定死（乘 procedural 经验、不乘 stated 事实）；实现要接 §5.6 的 `MEMORY_UPDATE`，**需 `[PROTOCOL_REQUEST]`** |

下表是三处升级的原始理由，保留备用：

| # | 现状 | 问题 | 建议 |
| --- | --- | --- | --- |
| R1 | L2 只查「绝对路径 / `..` / 少量危险词」（`server/contract/lint.py:143-201` 是黑名单） | 黑名单永远漏：`python -c "import shutil; shutil.rmtree(...)"` 不含任何危险词 | 改成**命令白名单**（`pytest` / `ruff` / `tsc` / `node scripts/*.mjs`），白名单放 `config/contract.yaml`；黑名单保留作第二道 |
| R2 | L4「至少 1 条 required 的 deterministic 标准；纯语义任务例外，但必须有 human 标准」 | 「纯语义任务」没有判定方式，实现时只能靠调用方自称 | 判定方式写死：`artifact.kind ∈ {report_md, message, chart}` **且** 没有任何文件可断言时，才允许走 human 分支；其余一律要求 deterministic |
| R3 | `promotion_weight` 字段存在（`model.py` 默认 1.0 / 0.4 / 1.0），但没有消费方 | 字段是死的，谁也不知道它该乘在哪 | 它乘的是 **procedural 经验**的权重，不乘 **stated 事实**的置信度（见 5.6）。语义先定，再接实现 |

R1 与 R3 是 M1 的阻塞项；R2 是 M1 的可选项（不影响验收闭环能不能跑通）。
**2026-10-22：R1 已解（已实现），R3 仍是阻塞项（等协议放行）。**

---

# 第二部分：§4 – §9（2026-10-21 补齐，草案 v0.2）

> 版本说明：§0–§3.7 与 §3.8 为 v0.1 及同日补写的正文；以下为 §4–§9。
> 与代码的对账基准：`HEAD 5f4d8f5`（2026-10-21 本机核对，`python scripts/check_protocol.py` 八类全 PASS）。
> §4–§9 中凡涉及**新增 WS 事件 / 新增台账字段**的，一律标注「需 `[PROTOCOL_REQUEST]`」，
> 在 Role 0 批准前不得实现。规范本身不是协议变更。

## 4 Task 与 Step 双层状态机

### 4.1 为什么必须拆两层

v0.1 的六态（SPEC_LOCKED / DISPATCHED / EXECUTING / VERIFYING / BLOCKED / ACCEPTED）
描述的是**一份合同**的生命周期，而实际执行是 **DAG 并发**。用一个 `TASK_STATE_CHANGE{agent}` 表达并发，
必然丢失「谁在跑第几步、哪一步卡住」——这是审阅意见 P0-1 指出的硬伤。

因此：

- **Task 级**：合同的生命周期。一个 `task_id` 同一时刻只有一个 Task 状态。
- **Step 级**：DAG 节点的生命周期。一个 Task 下可以有 N 个 Step 同时处于 RUNNING。
- 事件分两种：`TASK_STATE_CHANGE`（Task 级）与 `STEP_STATE_CHANGE`（Step 级），**不合并**。
- 契约只有一份，Step 不持有契约副本；Step 的验收标准通过 `criterion_ids` 引用 `TaskContract.acceptance`。

### 4.2 状态集

**Task 状态（13）**

| 状态 | 含义 | 终态 |
| --- | --- | --- |
| `CLARIFYING` | 契约未成形，正在追问/补槽 | 否 |
| `PROPOSED` | 契约草案已出，等用户确认 | 否 |
| `SPEC_LOCKED` | 契约冻结（`version` 只读） | 否 |
| `DISPATCHED` | 已选将、已下发派单密件 | 否 |
| `EXECUTING` | 至少一个 Step 在跑 | 否 |
| `VERIFYING` | 产出物移交 Verifier | 否 |
| `BLOCKED` | 暂停等人工决策/授权，带 `reason` 与 `resume_to` | 否 |
| `ACCEPTED` | 验收全过，等交付确认 | 否 |
| `DELIVERING` | 正在执行副作用（发送/覆盖/删除），由确定性代码执行 | 否 |
| `DELIVERED` | 副作用全部完成且记入 `effect_log` | **是** |
| `CANCELLED` | 用户取消，副作用已对账 | **是** |
| `FAILED` | 不可重试失败（契约违规、预算耗尽、未声明副作用） | **是** |
| `TIMEOUT` | 超过 `budget.max_wall_s`，副作用已对账 | **是** |

- `BLOCKED.resume_to ∈ {SPEC_LOCKED, DISPATCHED, EXECUTING, VERIFYING, DELIVERING}`，**必须显式给出**：
  没有 `resume_to` 的阻塞等于把任务丢进黑洞。
  （**2026-10-22 补 `DISPATCHED`**：原集合漏了它，而 T11 明写 `resume_to=DISPATCHED`——
  按原集合 T11 产出的状态违反本节自己的约束。以 T11 为准补进集合，不是删 T11。）
- `BLOCKED.reason ∈ {need_clarification, need_approval, need_authorization, retry_exhausted, ambiguous_result}`。
  前两类对应台账既有的 `blocked_kind`（`clarification` / `approval`），后三类是 v2 新增语义（见 4.9）。
  五个取值**每个都要有产生它的迁移行**，对照如下。**2026-10-22 改前只有 `need_clarification` 没有产生者**
  （判据：`reason` 集合 ∩ 每行 T 表文本，脚本跑原版得到 `need_clarification <- NONE`；
  这是机械核对，不是数的）：

  | reason | 由哪条迁移产生 |
  | --- | --- |
  | `need_clarification` | **T17**（存在 `kind=human` 的 required 标准，见 SM-17）。补它的理由：SM-17 要求在 `VERIFYING` 停下等人判，而停下必须落成带 `resume_to` 的 `BLOCKED`，T17 本来就是唯一承载 human 标准的迁移 |
  | `need_approval` | T9（无可用适配器，选择「请示」而非「要授权」）、T21（交付目标拒绝 / 需重授权） |
  | `need_authorization` | T9（无容量 / 无适配器）、T13（`E_PERMISSION`） |
  | `retry_exhausted` | T13、T17（重试耗尽或签名重复） |
  | `ambiguous_result` | T11（全部 Step 无回执且超 `dispatch_ack_timeout`） |

  **只补到能消除矛盾为止，不顺手扩大**：本轮**没有**把 `need_clarification` 塞进 T13，
  尽管 `§4.6` 说 `E_INPUT` 的默认去向是 `BLOCKED(need_clarification)`。
  原因：`E_INPUT` 是失败分类层的东西，把它挂到哪条 Task 迁移上属于**新决定**，
  不是修矛盾。这一条留白送裁决（见下面「待拍」）。

  同理 `§4.6` 的 `E_BUDGET` 去向写成「`FAILED` 或 `BLOCKED(need_approval)`」，二选一判据没写。
  本轮**没有**替它选：迁移表保持原样（T14 的 `budget_hard_exhausted` → `FAILED`），
  `BLOCKED(need_approval)` 那条路的触发条件待定。

  **待拍（两处，都不阻塞 M1 #1/#2）**：
  1. `E_INPUT`（输入不可达）在 `EXECUTING` 阶段出现时，走 `T13(BLOCKED)` 还是别的？走的话 `reason` 取 `need_clarification` 还是 `retry_exhausted`？
  2. `E_BUDGET` 软超限（`max_tokens` / `max_cost_usd` 告警后）走 `FAILED` 还是 `BLOCKED(need_approval)`？
  两处都只影响 `T13` 的 `reason` 取值域，改一行即可；在拍板前 `T13` 的取值域**保持**
  `{retry_exhausted, need_authorization}` 不变。

**Step 状态（11）**

| 状态 | 含义 | 终态 |
| --- | --- | --- |
| `CREATED` | 已生成，等待依赖 | 否 |
| `READY` | 依赖已满足，可派发 | 否 |
| `DISPATCHED` | 已交给 Worker，**尚无执行回执** | 否 |
| `RUNNING` | 已收到执行回执（pid / 首个事件） | 否 |
| `OBSERVED` | 执行结束，已拿到产出物 | 否 |
| `VERIFIED` | 该 Step 的验收标准全过 | **是** |
| `REJECTED` | 产出物不满足标准，可重做 | 否 |
| `FAILED` | 执行失败且不再重试 | **是** |
| `UNKNOWN` | 已派发但结果未知（超时/断连），**禁止直接重放** | 否 |
| `SKIPPED` | 依赖失败或条件不成立，跳过 | **是** |
| `CANCELLED` | 随 Task 取消 | **是** |

`DISPATCHED` 与 `RUNNING` 必须分开：前者只证明「发出去了」，后者才证明「对方在执行」。
混为一谈，就是「传输超时 = 工具没执行」这个经典误判（见 4.8）。

### 4.3 与现有台账七态的对账

现有台账只有七个状态（`server/interaction/threads.py:517` 的 `STATUSES`，
迁移边在 `:520` 的 `TRANSITIONS`，`done` 是唯一终态）。v2 的十三个状态**不替换它**，
而是通过一层**单向投影**落到台账：

| v2 Task 状态 | 台账 `status` | 说明 |
| --- | --- | --- |
| `CLARIFYING` | （无行） | 纯语音追问，不建单：建了单又删，世界会闪一下 |
| `PROPOSED` | `drafting` | 契约卡出现即落一行，世界才能显示「她在拟合同」 |
| `SPEC_LOCKED` | `briefing` | 冻结后才有权派单（原则 P1） |
| `DISPATCHED` | `briefing` | 台账没有独立派发态；`packet` 落库即派发凭据 |
| `EXECUTING` | `running` | |
| `VERIFYING` | `auditing` | |
| `BLOCKED` | `blocked` | `blocked_kind` 映射见 4.9 |
| `ACCEPTED` | `ready_to_report` | 「初审过了、等老板点头」，**不等于成功** |
| `DELIVERING` | `ready_to_report`（停留） | 副作用执行期间台账不动，避免出现「已交付」的假象 |
| `DELIVERED` | `done` | 唯一终态 |
| `CANCELLED` | `done` + `closed_reason=cancelled` | **需 `[PROTOCOL_REQUEST]`**：新增只读字段 |
| `FAILED` | `blocked`（`blocked_kind=approval`）或 `done` | 见 4.9 |
| `TIMEOUT` | `blocked` | 见 4.9 |

**投影的三条铁律**

1. **单向**：Task 状态决定台账状态；台账状态**不得反推** Task 状态。
   `ready_to_report` 同时可能是 `ACCEPTED` 与 `DELIVERING`，从台账反推必然错。
2. **未知值降级**：v1 客户端读到不认识的台账状态按「进行中」处理（PROTOCOL §19 既有约定），
   因此**不得**为了 v2 往台账塞新状态值；新状态值一旦出现，像素世界会静默误判。
3. **旧客户端零改动**：§4 的 13 态只存在于 TaskEngine 与 v2 事件中。
   没有 v2 事件时，前端退化为今天的 `commission_update` + REST 对账，
   这正是 `pixel-world/src/world/ledger.ts` 现在的做法（4 秒轮询兜底、30 秒 REST 对账）。

### 4.4 Task 级迁移表

列含义：**来源 → 目标｜触发｜守卫（全部满足才允许）｜发出的事件**。
守卫里的 `risk` / `budget` / `side_effects` / `acceptance` 均指 `TaskContract` 的同名字段。

| # | 来源 → 目标 | 触发 | 守卫 | 事件 |
| --- | --- | --- | --- | --- |
| T1 | `CLARIFYING → CLARIFYING` | 用户回答，仍有缺口 | 追问轮数 < `max_rounds`(3)，且缺口集合发生变化 | `ASK_USER` |
| T2 | `CLARIFYING → PROPOSED` | 缺口清零，或轮数耗尽 | `GateResult.status == ready`；或已用保守默认值补齐、缺口槽位全部 `highlight=true` | `CONTRACT_PROPOSED` |
| T3 | `CLARIFYING → CANCELLED` | 用户放弃 / 静默超时 | 无 | `TASK_STATE_CHANGE` |
| T4 | `PROPOSED → CLARIFYING` | 用户提出修改，产生新缺口 | 契约 `version` 递增 | `TASK_STATE_CHANGE` |
| T5 | `PROPOSED → SPEC_LOCKED` | `CONFIRM_CONTRACT` | 命令里的 `contract_id`+`version` 与当前一致；`risk ∈ {medium, high}` 时 `confirmed_slots ⊇ highlighted_slots` | `TASK_STATE_CHANGE` |
| T6 | `PROPOSED → SPEC_LOCKED` | 契约卡倒计时结束 | `risk == low`；`side_effects` 为空或全部 `reversible && target_class==local`；无 `highlight` 槽位；**且**确认门策略已批准（见 4.7 冲突项） | `TASK_STATE_CHANGE` |
| T7 | `PROPOSED → CANCELLED` | 卡片超时未确认 / 用户取消 | 无 | `TASK_STATE_CHANGE` |
| T8 | `SPEC_LOCKED → DISPATCHED` | 派单 | `permissions_required` 全部已授予；有可执行 Worker；`budget` 未超；契约 `version` 仍为冻结版 | `STEP_STATE_CHANGE`（首批 Step → READY） |
| T9 | `SPEC_LOCKED → BLOCKED` | 无可用适配器 / 无容量 | `reason=need_authorization` 或 `need_approval`；`resume_to=SPEC_LOCKED` | `ASK_USER` |
| T10 | `DISPATCHED → EXECUTING` | 任一 Step 进入 `RUNNING` | 至少一条 `dispatch_receipt` 存在 | `TASK_STATE_CHANGE` |
| T11 | `DISPATCHED → BLOCKED` | 全部 Step 均无回执且超过 `dispatch_ack_timeout` | `reason=ambiguous_result`；`resume_to=DISPATCHED` | `ASK_USER` |
| T12 | `EXECUTING → VERIFYING` | 全部 Step 终态且已交产出物 | 没有 `UNKNOWN`/`DISPATCHED` 的 Step；未发现未声明的副作用 | `VERIFICATION_REPORT` |
| T13 | `EXECUTING → BLOCKED` | Step 失败且 Step 重试耗尽 / 熔断 / 需授权 | `reason ∈ {retry_exhausted, need_authorization}`；`resume_to=EXECUTING` | `ASK_USER` |
| T14 | `EXECUTING → FAILED` | 契约违规 | 出现未在 `side_effects` 声明的副作用，或 `budget` 硬耗尽 | `TASK_STATE_CHANGE` |
| T15 | `EXECUTING → TIMEOUT` | 墙钟超限 | 已对账全部在飞副作用（见 4.8） | `TASK_STATE_CHANGE` |
| T16 | `VERIFYING → EXECUTING` | 存在 `required` 标准未过 | 任务级 `attempt < budget.max_retries`；本次失败签名 ≠ 上次签名 | `TASK_STATE_CHANGE` |
| T17 | `VERIFYING → BLOCKED` | 同上但不可自动重试 | `attempt ≥ max_retries`，或签名重复 → `reason=retry_exhausted`；或存在 `kind=human` 的 `required` 标准 → `reason=need_clarification`；两者并存时取 `need_clarification`（要人来判，比「再试也没用」更具体）；`resume_to=VERIFYING` | `ASK_USER` |
| T18 | `VERIFYING → ACCEPTED` | 验收全过 | 所有 `required` 标准通过；`semantic` 标准每条都带证据 | `VERIFICATION_REPORT` |
| T19 | `ACCEPTED → DELIVERING` | 交付确认 | `risk==low` 且无副作用 → 直接进入；`risk∈{medium,high}` → 必须 `APPROVE_SIDE_EFFECT`；**`risk==low` 且有副作用 → 按「有副作用」处理，必须 `APPROVE_SIDE_EFFECT`**（见下注） | `TASK_STATE_CHANGE` |
| T20 | `DELIVERING → DELIVERED` | 副作用全部完成 | 每条 `side_effects[].idempotency_key` 在 `effect_log` 里恰好一条成功记录 | `TASK_STATE_CHANGE` |
| T21 | `DELIVERING → BLOCKED` | 目标拒绝 / 需要重授权 | `reason=need_approval`；`resume_to=DELIVERING` | `ASK_USER` |
| T22 | `BLOCKED → resume_to` | `ANSWER` / 授权 / 人工改判 | 回答非空；或授权对象命中了阻塞时的 `side_effect.id` | `TASK_STATE_CHANGE` |
| T23 | `BLOCKED → CANCELLED` | 用户取消 | 无 | `TASK_STATE_CHANGE` |
| T24 | 任意非终态 → `CANCELLED` | `CANCEL_TASK` | 已对账在飞副作用；未完成的 Step 全部置 `CANCELLED` | `TASK_STATE_CHANGE` |

**重试次数口径**：`budget.max_retries` 数的是**验收打回**次数（T16），不是 Step 内部工具重试
（`budget.max_step_retries` 管那个）。这两个数混用，就会出现「重试了 3 次却只打回 1 次」的争议。

> **T19 补第三支的由来（2026-10-22，Agent 集成工程师在实现时发现）**：
> 原文只写了两支——「`risk==low` 且无副作用」与「`risk∈{medium,high}`」，
> **`risk==low` 但有副作用**这个组合落在两者之间的缝里，规范没说该怎么办。
> 补的判据只有一句：**要不要批准取决于「有没有副作用」，与 `risk` 无关**——
> 因为 `APPROVE_SIDE_EFFECT` 防的是「会改变世界的那一下」，而 `risk` 是按槽位置信度算的，
> 两者是不同维度（§3.4 与 §6.2 各自定义）。所以有副作用就要点头，`risk=low` 不能豁免。
> **这条不是新决定，是把原文漏掉的组合按原文自己的逻辑补全**；如果判定它应当算新决定，
> 改这一注即可，迁移表不用动。

> **枚举式与布尔式（2026-10-22，UI/UX 设计师核对实现与图时发现）**：
> 规范的三支是**枚举**（列出三种组合各自怎么走），而 `server/task/machine.py` 的
> `_t19_guard`（`:267-273`）写成了**布尔式**（先判有没有批准，再判 `risk=low 且无副作用`，
> 其余拒绝）。**当前两种写法等价**，但分叉点是「将来加第四支」时：
> 布尔式加一个 `or` 条件就可能悄悄放行枚举里没有的组合，而枚举加一支是显式的。
> **所以判据以枚举为准**：实现可以写成布尔式（更短），但每次改那个 guard，
> 都要回来对一遍这三支。`machine.py:580` 的 trigger 文本已经写成布尔式，
> 它描述的是实现，不是判据。

**T12 的一处冗余是故意的**：守卫同时查「全部 Step 终态」与「没有 `UNKNOWN`/`DISPATCHED`」，
而 `UNKNOWN` 本身就不是终态（§4.2），所以后一句在当前定义下是冗余的。
保留它是因为**「UNKNOWN 不是终态」这个前提将来若被改动，这层检查会先红**——
冗余在这里是给未来留的报警器，不是笔误。

### 4.5 Step 级迁移表

| # | 来源 → 目标 | 触发 | 守卫 | 事件 |
| --- | --- | --- | --- | --- |
| S1 | `CREATED → READY` | 依赖全部 `VERIFIED`（或无条件依赖） | 无 | `STEP_STATE_CHANGE` |
| S2 | `CREATED → SKIPPED` | 任一依赖 `FAILED/SKIPPED`，或条件不成立 | Task 未进入终态 | `STEP_STATE_CHANGE` |
| S3 | `READY → DISPATCHED` | 交给 Worker | 该 Step 所需权限已授予；并发未超 `max_concurrent` | `STEP_STATE_CHANGE` |
| S4 | `DISPATCHED → RUNNING` | 收到执行回执 | `dispatch_receipt` 非空（pid / 首个 progress / HTTP 202） | `STEP_STATE_CHANGE` |
| S5 | `DISPATCHED → UNKNOWN` | 超过 `dispatch_ack_timeout` 无回执 | 已写 `dispatch_receipt`（证明「可能已发出」） | `STEP_STATE_CHANGE` |
| S6 | `RUNNING → OBSERVED` | Worker 交出产出物 | 产出物路径在契约范围内（lint L2 同款判定） | `TOOL_EXECUTION`(done) |
| S7 | `RUNNING → FAILED` | 执行报错 | Step 内部重试已耗尽，或错误不可重试 | `TOOL_EXECUTION`(failed) |
| S8 | `RUNNING → UNKNOWN` | 超过 `budget.max_wall_s` 或连接中断 | 已写 `dispatch_receipt` | `TOOL_EXECUTION`(unknown) |
| S9 | `OBSERVED → VERIFIED` | Step 级断言全过 | 该 Step `criterion_ids` 全部通过 | `VERIFICATION_REPORT` |
| S10 | `OBSERVED → REJECTED` | 断言未过 | 存在失败断言 | `VERIFICATION_REPORT` |
| S11 | `REJECTED → READY` | 重做 | Step 重试 < `max_step_retries`；失败签名 ≠ 上次 | `STEP_STATE_CHANGE` |
| S12 | `REJECTED → FAILED` | 不再重做 | 重试耗尽，或签名重复 | `STEP_STATE_CHANGE` |
| S13 | `UNKNOWN → OBSERVED` | 对账确认已产生产出物 | 对账证据非空 | `TOOL_EXECUTION`(reconciled) |
| S14 | `UNKNOWN → READY` | 对账确认未执行 | 对账证据非空（「查过，没执行」才算） | `STEP_STATE_CHANGE` |
| S15 | 任意非终态 → `CANCELLED` | Task 取消 / 熔断 | Worker 的 `cancel()` 已返回 | `STEP_STATE_CHANGE` |

**`UNKNOWN` 的安全出口只有两条**：对账到「已执行」或「未执行」。**没有第三条**。
「等一会儿再试一次」不是出口——那正是重复付款、重复删除的来源。

### 4.6 失败分类与签名

v0.1 只说「重试 3 次」，太粗。按失败**类型**决定去向，按**签名**判断是不是同一个错。

| 类别 | 触发例 | 可自动重试 | 默认去向 | 签名组成 |
| --- | --- | --- | --- | --- |
| `E_SPEC` | 契约有歧义、验收标准互相矛盾 | 否 | `PROPOSED`（回炉改契约） | `contract_id + version + 冲突字段` |
| `E_INPUT` | 输入路径不存在、URL 不可达 | 否 | `BLOCKED(need_clarification)` | `input.uri + 错误码` |
| `E_TOOL` | Worker 子进程报错、工具调用失败 | 是 | Step `REJECTED`→`READY` | `step_id + 工具名 + 归一化错误串` |
| `E_CRITERION` | 产出物存在但断言不过 | 是 | Task `VERIFYING→EXECUTING` | `criterion_id + 断言名 + 实际值摘要` |
| `E_BUDGET` | token / 时间 / 成本超限 | 否 | `FAILED` 或 `BLOCKED(need_approval)` | `budget 字段 + 实际值` |
| `E_PERMISSION` | 需要未授予的权限 | 否 | `BLOCKED(need_authorization)` | `permission + 请求方` |
| `E_EFFECT` | 出现未声明的副作用 | 否 | `FAILED` | `side_effect.type + target` |
| `E_AMBIGUOUS` | 派发后无回执、结果未知 | 否（先对账） | Step `UNKNOWN` | `dispatch_receipt` |

**签名归一化**（与 `server/assistant/telemetry.py:69` 的 `normalize_signature` 同一思路）：
去掉数字、路径、时间戳、随机 id，只保留「动作 + 参数键 + 错误类别」。
签名相同 = 同一种错误；同一种错误第二次出现就不该再试。

### 4.7 重试与确认策略（替换 v0.1 的「固定 3 次」）

1. **Task 级**：`attempt` 只在 T16 递增。两次打回之间，若 `signature` 相同 → 直接 `BLOCKED`，
   不消耗剩余次数（省的是用户的时间，不是预算）。
2. **Step 级**：`max_step_retries` 默认 2，同样受签名约束。
3. **预算**：`max_wall_s` 是硬顶；`max_tokens` / `max_cost_usd` 超限先告警、再拒绝继续，
   已产生的产出物保留（「部分产物留待人工核」是既有纪律，见 PROTOCOL §19）。
4. **重试必须带结构化失败原因**：`{class, signature, evidence}` 一起进下一轮的派单密件
   （`BriefingPacket.feedback` 已有位置，`server/assistant/dispatcher.py:72`）。只换句话重说不算重试。

> **冲突项（必须在 M1 前拍板）**：v0.1 §3.4 允许 `risk=low` 的任务用 3 秒倒计时自动开工；
> 但 `docs/ADR_AGENT_BRIDGE.md:56-60` 的 E3 确认门要求 `world.dispatch` 这类 mutation 必须用户确认。
> 两者不能同时成立。三选一：
> (a) 取消倒计时，一律显式确认（改动最小，世界侧零改动）；
> (b) 保留倒计时，但只对「纯只读、无副作用、无高亮槽位」的 low 任务，且必须在世界画面上显示可点取消；
> (c) 倒计时只作为**视觉引导**，到期后仍等确认。
> **默认建议 (a)**：一个说错话就要重开的语音系统里，自动开工省下的 3 秒抵不上一次误派的代价。

> 注：§3.3 与 §3.4 之间的口径冲突**已经有解**——`server/contract/gate.py:524` 的
> `effective_start_policy()` 用「OR 规则」合成两者（追问超限时，即使 `risk=low` 也不许倒计时）。
> 剩下的是**本文与 ADR 之间**的口径冲突，不在代码里，需要人来拍。

### 4.8 派发后不得重放（`UNKNOWN` 的落地）

传输层超时 **不等于** 工具没执行。派单已经发出去之后：

```
if 无 dispatch_receipt:            # 确定没发出
    可用同一幂等键重试 / 重新规划
elif 幂等 且 已对账确认远端去重:    # 确定可安全重放
    用同一 command_id 重试
else:                              # 其余一切
    Step → UNKNOWN，对账副作用
```

对账手段按 Worker 类型给出，**不能靠猜**：

| Worker 类型 | `dispatch_receipt` | 对账证据 |
| --- | --- | --- |
| `cli_subproc` | 子进程 pid + 启动时间（`server/assistant/adapters/cli_subproc.py:81` 起得来就有） | 工作区文件快照哈希 + Worker 的 stdout 事件流 |
| `http_api` / `mcp` | 请求 id / HTTP 202 + 响应头 | 远端查询接口；没有查询接口一律视为 `UNKNOWN` 直到人工 |
| `durable`（sidecar） | sidecar 的 lease id | sidecar 的 memo / 效果日志 |

**工作区快照**：Step 派发前对 `ArtifactSpec.location` 所在目录做一次路径+大小+哈希的清单，
结束（或 `UNKNOWN`）后再做一次。两次差集就是「这一步到底动了什么」——这也是 `E_EFFECT`
判定「出现了未声明副作用」的唯一可靠依据。

### 4.9 取消、失败、超时如何落账（不新增台账状态）

v2 有 `CANCELLED` / `FAILED` / `TIMEOUT`，台账没有。方案：

| v2 | 台账 | 额外落点 |
| --- | --- | --- |
| `CANCELLED` | `done` | 需新增只读字段 `closed_reason`（`cancelled` / `user_abort`）——**需 `[PROTOCOL_REQUEST]`**；批准前只在 `commission_events` 里记一条 `kind=cancelled` |
| `FAILED` | `blocked`（`blocked_kind=approval`） | `commission_events` 记 `kind=failed` + `class` + `signature` |
| `TIMEOUT` | `blocked` | `commission_events` 记 `kind=timeout` + 已用墙钟 |

**为什么不直接加状态值**：PROTOCOL §19 的迁移表是硬约束（`threads.py:517-527`），
加一个状态就要改 `STATUSES`、`TRANSITIONS`、前端 `default` 分支、像素世界的
`ACTIVE_STATUSES`/`FINISHED_STATUSES`（`pixel-world/src/world/projection.ts`）四处，
且旧客户端的「未知状态降级为进行中」会让 `done` 与 `cancelled` 看起来一样。
**事件表只追加不改写**（PROTOCOL §19），用它承载细分原因，成本最低、回滚最容易。

**已知缺口（诚实记录）**：今天的 `cancel` 直接把单迁到 `done`（`server/assistant/dispatcher.py` 的 `Dispatcher.cancel()`），
不写取消原因、不区分「用户取消」与「正常归档」。像素世界因此会把取消的单当成「已交付」，
还给信仰加点（`pixel-world/src/world/simulation.ts` 的交付结算路径）。
**这是本轮查出来的真实缺陷**，修法就是上表的 `closed_reason`。
（**2026-10-22 服务端已修**：`fa488ea`。实现用已有的 `audit` JSON 承载 `cancelled` / 原因 / 时刻，**没有新增 `closed_reason` 字段**，因此未触发协议变更。
　**但世界侧没修**，这一句原先写成「已修」是不准确的，2026-10-22 由 World Builder 实测发现：
　`pixel-world/src/world/projection.ts` 全文不读 `audit`，`types.ts` 也没有该字段，
　所以取消单（`status=done`）在世界里仍然 `deliveries.ledger=1`、`faith=1`、日志写「已交付」，
　与验收通过的单完全相同。要让世界侧认得出取消单，得动 `types.ts` + `projection.ts`，
　且这两个文件与用户本人的 Claude Code 线地盘重合，须先报批。）

**同类的第二处缺口**：像素世界把 `ready_to_report` 也算作「收工」
（`pixel-world/src/world/projection.ts` 的 `FINISHED_STATUSES = {ready_to_report, done}`），
于是一单在**老板验收之前**就走完交付动画、还加了信仰点（`simulation.ts` 的 `finishActiveTask` → `accrueFaith`）。
这与 `docs/ADR_AGENT_BRIDGE.md` §4.3 自己写的「待验收不是成功」直接冲突，也与「完成权在台账」不符：
台账的 `ready_to_report` 是「等验收」，`done` 才是「验收过」。
修法：`FINISHED_STATUSES` 只留 `done`；`ready_to_report` 归入「待你过目」，世界显示一个**未盖章的宝箱**停在质检台，
而不是送出 + 加信仰。这一条只改世界侧，不用动协议。
（**2026-10-22 已修 `FINISHED_STATUSES`**：`6473b96`，只留 `done`。
　**世界层还有一个条件性缺陷，2026-10-22 两次实测、结论修正过一次，最终定性如下。**

　**路径**：`ledger.ts:338` 的 `publish()` 必经 `project()`，`projection.ts:97` 把 `ready_to_report`
　剔出 `tasks`，所以 `onChange` 载荷里 `tasks=[]`、`finished=[]`。此时 `simulation.ts::sync`
　的清理条件只有两条——`finishedIds.has(id)` 或 `(complete && !next.has(id))`——
　而 `ready_to_report` 这一单既不在 `finished` 也不在 `next`，两条都不成立。

　**它能否自愈，取决于 `complete`，而 `complete` 取决于台账单数**：
  - `complete` 只在 REST 全量 `refresh()` 时赋值（`ledger.ts:305`：`items.length < COMMISSIONS_LIMIT`）；
    `applyPush`（WS 增量）不碰它。
  - 服务端 `threads.py:794` 把 limit **钳制**到 `MAX_LIST = 100`（`threads.py:40`）。
  - 所以**单数 < 100 → 满页判 false → `complete=true` → `abandonActiveTask` → 角色正常收手**；
    **单数 ≥ 100 → REST 返回满页 → `complete=false` → 两个条件都不成立 →
    任务留在 `this.tasks` 里但再也收不到更新 → 角色永久 `mode=working`、`visible=false`、
    `isFree()` 不认它 → 接不了新活。**

　**实测**（两种载荷都跑过，2026-10-22）：
  - REST 返回 0 条（单数少）→ `mode=idle`，**不卡**；
  - REST 返回 100 条（满页）→ 两条断言全红（`actual: 100` / `不该有角色永久不可见地卡在 working`）→ **卡**。

　**两次结论修正的记录**：World Builder 第一次用**伪造** `incoming` 测出「永久卡死」，
  被 Chief 指出探针绕过 `ledger.ts`、走了生产不存在的路径；第二次走真实链路测出「不卡」，
  报「缺陷不成立」——**但那次 REST 只返回 0 条，正好落在能自愈的那一半**。
  最终定性由 Chief 补测满页载荷得出。**「真实链路」还不够，要选会走到缺陷的那条分支。**

　**回归锁**：`pixel-world/test/ready-to-report-stuck.test.ts`（175 行）。
  它当前的 REST 夹具返回 0 条，**因此它现在锁的是「不卡」那一半**；
  要锁缺陷必须让夹具返回满页 100 条，并加一条 `assert.equal(payload.complete, false)` 前提断言
  （否则将来有人改分页 limit，测试会悄悄滑回能通过的状态）。

　**根因不在 `project()`，在 `sync` 的清理条件没覆盖「从活动集合消失但没进 finished」这一种。**
　修法要动 `simulation.ts` 的清理条件，不碰 `types.ts`。）

**待拍（世界侧观察，2026-10-22）**：`ready_to_report` 时**尚在干活的角色**怎么办？
规范只说了「世界显示一个未盖章的宝箱停在质检台」，没说人怎么办。而实测显示当前行为是
**立刻撤出铺子**（`abandonActiveTask`），且在单数 ≥ 100 时这个撤出会失败、角色卡死。
两个选择：(a) 继续在屋里干到底，等老板点头再走交付；(b) 立刻撤出铺子、在质检台旁待命等验收。
这一条改的是「世界看起来像什么」，必须用户定；定了之后 `simulation.ts` 与 §5 的动画对不上就是缺陷。

### 4.10 验收用例（Given / When / Then，可直接进评测集）

| ID | Given | When | Then |
| --- | --- | --- | --- |
| SM-01 | 契约 `risk=low`、无副作用、无高亮槽位 | 用户确认 | `PROPOSED→SPEC_LOCKED`，事件带 `confirmed_by=user` |
| SM-02 | 契约 `risk=medium` | 卡倒计时结束 | **不**迁移；仍停在 `PROPOSED` |
| SM-03 | 契约 `risk=high`，有 2 个高亮槽位 | 用户只确认了 1 个 | 拒绝 `CONFIRM_CONTRACT`，返回缺失槽位清单 |
| SM-04 | 契约 `version=2`，用户确认的是 `version=1` | 发 `CONFIRM_CONTRACT(v1)` | 拒绝，事件 `CONTRACT_STALE` |
| SM-05 | 第一次验收打回，签名 S1 | 第二次打回，签名仍是 S1 | 直接 `BLOCKED(retry_exhausted)`，不再消耗第 3 次 |
| SM-06 | 第一次打回 S1，第二次 S2 | 第三次 S3 | 三次都用尽后才 `BLOCKED` |
| SM-07 | 全部 Step `VERIFIED`，但有一步处于 `UNKNOWN` | 尝试 `EXECUTING→VERIFYING` | 拒绝，`reason=unresolved_steps` |
| SM-08 | 产出物路径 `../../etc/passwd` | Step 交产出物 | `OBSERVED` 被拒，`E_CRITERION`（越界） |
| SM-09 | 契约只声明 `overwrite_file` | 执行中出现 `send_message` | `EXECUTING→FAILED`，`E_EFFECT` |
| SM-10 | `ACCEPTED`，`risk=high` | 未经 `APPROVE_SIDE_EFFECT` 就交付 | 拒绝；`DELIVERING` 不成立 |
| SM-11 | `DELIVERING`，同一 `idempotency_key` 被触发两次 | 第二次执行 | 第二次直接返回首次结果，不产生第二个副作用 |
| SM-12 | Worker 在 `RUNNING` 时断开 | 立刻重试 | 拒绝；先 `UNKNOWN` 再对账 |
| SM-13 | `UNKNOWN`，对账发现文件已写出 | 对账 | `UNKNOWN→OBSERVED`，不重跑 |
| SM-14 | `UNKNOWN`，对账确认未执行 | 对账 | `UNKNOWN→READY`，可用同一幂等键重派 |
| SM-15 | `BLOCKED(need_authorization)`，用户授权 | `ANSWER` | 回到 `resume_to`，且授权只对当次 `side_effect.id` 有效 |
| SM-16 | `EXECUTING`，用户说「算了」 | `CANCEL_TASK` | Step 全部 `CANCELLED`，Worker `cancel()` 已返回，Task `CANCELLED` |
| SM-17 | `VERIFYING`，存在 `kind=human` 的 required 标准 | 自动判定全过 | 迁到 `BLOCKED(need_clarification)` 并等 `ANSWER`，`resume_to=VERIFYING`；**不**直接 `ACCEPTED`（2026-10-22 改：原写「停在 VERIFYING 等」，但停在非终态等于没有阻塞记录的悬挂，与 §4.2「没有 `resume_to` 的阻塞等于把任务丢进黑洞」冲突，以 T17 为准） |
| SM-18 | 任意状态收到一个不在迁移表里的目标 | 迁移 | 拒绝并记 `kind=illegal_transition`，状态不变 |

**属性测试**（比逐条用例更省）：随机生成 10 万条迁移序列，断言
(1) 永不进入未列出的 (from,to)；(2) 终态没有出边；(3) `BLOCKED` 必带 `resume_to`。

## 5 事件协议 v2

> **状态：提案，未生效。** 本章新增的事件与字段一律需要 `[PROTOCOL_REQUEST]` 走 Role 0；
> `scripts/check_protocol.py` 的第四类扫描会拦住任何「后端发了、前端没接」的新类型。
> 落地顺序必须是：协议评审 → 加事件 → 同时改 `docs/PROTOCOL.md` 与 checker → 再接前端。

### 5.1 信封

```ts
type TaskId = string;
type StepId = string;

interface EventEnvelope<T extends EventType, P> {
  v: 2;                       // 协议版本，与 v1 并存
  id: string;                 // 全局唯一，消费方按 id 去重（至少一次投递）
  seq: number;                // 单调递增，断线后按它补齐
  ts: number;                 // 事件产生时刻（epoch ms，服务端时钟）
  type: T;
  task_id?: TaskId;           // 与任务无关的事件（如 AGENT_PRESENCE）可省略
  step_id?: StepId;           // 仅 STEP_* 事件有
  session_id?: string;        // 便于按会话回放
  turn_id?: string;           // 与语音轮次对齐（服务端 VoiceTurn 已经有这个字段）
  payload: P;
}
```

**三条设计取舍**

1. `seq` 是**连接级**单调，不是全局单调。多进程写同一 SQLite 时全局单调要么加锁要么引入
   单写者；单用户本地系统不值得为它付代价。断线补齐靠「`seq` + `after_seq` 查询」，
   不靠「每个包必达」。
2. `ts` 用服务端时钟。前端时钟不可信（用户可能改系统时间），用它排序会让回放错乱。
3. `v` 是**信封版本**，不是 schema 版本。schema 演进靠「未知字段忽略、未知 type 整条忽略」，
   不靠 `v` 递增——否则每加一个字段都要全链路发版。

### 5.2 事件清单

| type | 谁发 | 关键 payload | 前端表现（世界） | 投递保障 |
| --- | --- | --- | --- | --- |
| `TASK_STATE_CHANGE` | TaskEngine | `from,to,reason?,resume_to?,attempt,risk` | Task 级动画：接单/开工/打回/归档 | 至少一次 |
| `STEP_STATE_CHANGE` | TaskEngine | `from,to,agent,station?,attempt` | 具体小人走到工位、坐下、起身 | 至少一次 |
| `CONTRACT_PROPOSED` | IntentGate | `contract_id,version,goal,artifact,inputs_kind[],deliver_to_shown?,risk,highlighted_slots[],criteria[{id,text,kind}],budget`（**只发公开投影**：不带 `side_effects[].target` 明文、不带 `memory_block`） | 弹出《委托卷轴》 | 至少一次 |
| `ASK_USER` | TaskEngine / Gate | `question,options[],missing[],deadline?` | 头顶感叹号 + 选项胶囊 | 至少一次 |
| `TOOL_EXECUTION` | Worker 适配器 | `tool,status,steps_done?,steps_total?,detail?` | 屏幕闪烁 / 进度条 / 冒烟 | 至多一次（可丢） |
| `VERIFICATION_REPORT` | Verifier | `passed,findings[],criteria[{id,kind,passed,evidence?}]` | 质检台绿章 / 红灯黑烟 | 至少一次 |
| `DELIVERY_EFFECT` | 交付执行器 | `effect_id,type,target_class,status,idempotency_key` | 宝箱送出/入库 | 至少一次 |
| `MEMORY_UPDATE` | MemoryService | `action,memory_ref,source,confidence,provenance` | 档案室书架增删 | 至少一次 |
| `AGENT_PRESENCE` | 会话层 | `state:idle\|listening\|thinking\|speaking` | 头顶麦克风/气泡 | 至多一次（可丢） |

**没有 `TASK_SNAPSHOT` 事件。** 首帧对齐用 REST 全量（§5.4），再发一份快照就是造第二个真相源。

**改名说明**：v0.1 的 `VOICE_STATE` 改叫 `AGENT_PRESENCE`——世界前端不一定播语音，
把它绑死在「语音状态」上会让视觉通道被迫造第二个事件（审阅意见 P1-6）。
`AGENT_PRESENCE` 的 `state` 取值与现有 `turn_started` / `thinking` / `done` 语义对齐，
不引入第二套词汇。

**删掉的东西**：

- `progress(0-100)`——绝大多数工具给不出真实百分比，假进度条会破坏「画面等于真实状态」。
  改成 `steps_done / steps_total`，或者 `indeterminate: true`（世界显示呼吸灯而不是进度条）。
  像素世界已经在这么做：台账的活 `determinate: false`（`pixel-world/src/world/types.ts`），
  只有沙盒计时器才画百分比。
- `state: listening|thinking|speaking` 里塞 `speaking` 的同时又发音频流——两处真相。
  `AGENT_PRESENCE` 只表达**是否在出声**，不出声的时长由前端自己按音频包判断。

### 5.3 前端 → 后端命令

```ts
type Command =
  | { type: 'CONFIRM_CONTRACT'; task_id: TaskId; contract_id: string; version: number;
      confirmed_slots: string[]; request_id: string }
  | { type: 'ANSWER'; task_id: TaskId; answer: string; option_index?: number; request_id: string }
  | { type: 'CANCEL_TASK'; task_id: TaskId; reason?: 'user_abort' | 'wrong_task'; request_id: string }
  | { type: 'APPROVE_SIDE_EFFECT'; task_id: TaskId; effect_id: string; request_id: string }
  | { type: 'RETRY_STEP'; task_id: TaskId; step_id: StepId; request_id: string }
  | { type: 'ADJUST_MEMORY_CONFIDENCE'; memory_ref: string; action: 'up' | 'down' | 'delete';
      evidence_turn_id?: string; request_id: string };
```

- **每条命令都带 `request_id`**，服务端按它做幂等（与 `server/interaction/threads.py` 的
  `commission_requests` 表同一套做法）。断线重发不会建出第二单。
- `CONFIRM_CONTRACT` 必须带 `version`：否则用户确认的是「上一版契约」，而服务端已经推进到下一版
  ——这是语音场景最容易出的错（话说出口时卡片已经变了）。
- `ANSWER` 的 `option_index` 让「点胶囊」和「开口说」走同一条路径，不产生两套逻辑。
- `ADJUST_MEMORY_CONFIDENCE` 的 `up` **不是**直接改 `confidence` 数字：见 5.6。

### 5.4 快照与重连

**首帧对齐不靠新事件，靠 REST。** 现有台账接口已经是完整真相
（`GET /api/executive/commissions`，PROTOCOL §19），再发一遍 `TASK_SNAPSHOT` 等于造第二个真相源。
因此：

```
连接建立
  → GET /api/executive/commissions?limit=100       （快照，already implemented）
  → 同时开始收 v2 事件，缓存 seq ≤ snapshot_seq 的事件
  → 快照应用完毕，丢掉缓存里 seq ≤ snapshot_seq 的
  → 之后按 seq 连续应用
```

`snapshot_seq` 由 REST 响应头 `X-Event-Seq` 给出（**需 `[PROTOCOL_REQUEST]`**：新增响应头，
不是新路由，风险最低）。没有这个头时退化为「先拉快照、再等到第一个 seq 事件、
中间若有空洞就再拉一次快照」——像素世界现在的 4 秒轮询 + 30 秒对账就是这条退化路径
（`pixel-world/src/world/ledger.ts` 的 `RECONCILE_MS`），验证过可用。

**重连补齐**：`GET /api/events?after_seq=1234&limit=500`（**需 `[PROTOCOL_REQUEST]`**）。
事件流本身追加写 JSONL（见 §7），这个路由只是把 JSONL 读出来。
若 `after_seq` 早于最老的保留事件，返回 409，前端改为整份快照。

### 5.5 投递保障与背压

| 类别 | 事件 | 丢失后果 | 策略 |
| --- | --- | --- | --- |
| 关键 | `TASK_STATE_CHANGE` / `CONTRACT_PROPOSED` / `ASK_USER` / `VERIFICATION_REPORT` / `MEMORY_UPDATE` | 画面与真相不一致 | 至少一次 + 断线补齐 |
| 可丢 | `TOOL_EXECUTION` / `AGENT_PRESENCE` | 少闪几下 | 至多一次，慢消费者直接丢旧帧 |
| 幂等 | 全部 | 重复应用应无副作用 | 消费方按 `id` 去重；状态机迁移本身也是幂等的（同 from→to 再应用一次不变） |

慢消费者策略：每个连接一个**有界**队列（建议 256 帧）。队列满时，只丢「可丢类」；
「关键类」满了就断开连接，让前端走重连补齐——**宁可不显示，也不显示错的**。

### 5.6 记忆变更事件（`MEMORY_UPDATE`）

现有的记忆写入有硬约束，事件必须照着它设计，不能另造一套：

- 来源只有三档：`stated / inferred / uncertain`（`server/memory/store.py:24`）。
- 来源强度是有序的：`uncertain < inferred < stated`（`server/memory/store.py:588`）。
- `confidence` **不会因为「任务成功」自动上调**：只有「更强的来源 + 有效证据」才替换
  （`store.py:588-606`），或被 MemRL 的 Q 值单独调制（`server/memory/feedback.py`，
  配置在 `config/memory.yaml:132`）。
- 删除是物理硬删（`store.py:714`）。

因此 `MEMORY_UPDATE` 的语义只能是：

| action | 含义 | 对应真实操作 |
| --- | --- | --- |
| `promoted` | 来源升级（如一次 only-inferred 的偏好被用户明确复述，升为 `stated`） | `upsert` 一条更强来源 |
| `confirmed` | 用户当场确认「记得对」 | `feedback` 的 +1 reward（Q 值微调，不改 `confidence` 数字） |
| `corrected` | 用户当场纠正「不是这样」 | 新值 `supersede` 旧值 + 写一条 lesson |
| `deleted` | 用户手动删除 | 物理硬删 |
| `distilled` | 从打回中提炼出一条纠错规则 | 写 `lessons`（不是改旧记忆） |

**旧编号 §4.10（现 §5.6）的「验收通过→上调记忆置信度」必须改写**：
一次任务通过只证明「这次的做法可行」，不证明「这条偏好是真的」。
把前者写成后者，就是把错误放大成 SOP（审阅意见 P0-4）。
正确的飞轮是：**通过 → 写一条 procedural 经验（可回滚、带任务来源）**；
**用户明说 → 才动 stated 记忆**。两个轮子分开转。

### 5.7 示例事件脚本（同时是沙盘的播放源）

沙盘不要自己编动画，直接播这份脚本（JSONL，一行一个事件）：

```jsonl
{"v":2,"id":"e1","seq":1,"ts":1761000000000,"session_id":"s1","turn_id":"t88","type":"CONTRACT_PROPOSED","task_id":"ct_demo_001","payload":{"contract_id":"ct_demo_001","version":1,"goal":"更新竞品对比表并发给我","risk":"medium","highlighted_slots":["deliver_to"],"criteria":[{"id":"C1","kind":"deterministic"},{"id":"C4","kind":"semantic"}]}}
{"v":2,"id":"e2","seq":2,"ts":1761000003200,"session_id":"s1","type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"PROPOSED","to":"SPEC_LOCKED","confirmed_by":"user","attempt":0}}
{"v":2,"id":"e3","seq":3,"ts":1761000003300,"type":"STEP_STATE_CHANGE","task_id":"ct_demo_001","step_id":"s_fetch","payload":{"from":"CREATED","to":"READY","agent":"探马"}}
{"v":2,"id":"e4","seq":4,"ts":1761000003400,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"SPEC_LOCKED","to":"DISPATCHED"}}
{"v":2,"id":"e5","seq":5,"ts":1761000003600,"type":"STEP_STATE_CHANGE","task_id":"ct_demo_001","step_id":"s_fetch","payload":{"from":"DISPATCHED","to":"RUNNING","agent":"探马","station":"star_archive"}}
{"v":2,"id":"e6","seq":6,"ts":1761000003700,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"DISPATCHED","to":"EXECUTING"}}
{"v":2,"id":"e7","seq":7,"ts":1761000005000,"type":"TOOL_EXECUTION","task_id":"ct_demo_001","step_id":"s_fetch","payload":{"tool":"web_fetch","status":"running","steps_done":1,"steps_total":3}}
{"v":2,"id":"e8","seq":8,"ts":1761000009000,"type":"STEP_STATE_CHANGE","task_id":"ct_demo_001","step_id":"s_fetch","payload":{"from":"RUNNING","to":"OBSERVED"}}
{"v":2,"id":"e9","seq":9,"ts":1761000010000,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"EXECUTING","to":"VERIFYING"}}
{"v":2,"id":"e10","seq":10,"ts":1761000012000,"type":"VERIFICATION_REPORT","task_id":"ct_demo_001","payload":{"passed":false,"criteria":[{"id":"C3","kind":"deterministic","passed":false,"evidence":"第7行 更新时间=2026-10-20"}]}}
{"v":2,"id":"e11","seq":11,"ts":1761000012100,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"VERIFYING","to":"EXECUTING","attempt":1,"reason":"E_CRITERION"}}
{"v":2,"id":"e12","seq":12,"ts":1761000020000,"type":"VERIFICATION_REPORT","task_id":"ct_demo_001","payload":{"passed":true,"criteria":[{"id":"C1","passed":true},{"id":"C4","passed":true,"evidence":"4 条来源链接"}]}}
{"v":2,"id":"e13","seq":13,"ts":1761000020100,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"VERIFYING","to":"ACCEPTED"}}
{"v":2,"id":"e14","seq":14,"ts":1761000023000,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"ACCEPTED","to":"DELIVERING"}}
{"v":2,"id":"e15","seq":15,"ts":1761000023500,"type":"DELIVERY_EFFECT","task_id":"ct_demo_001","payload":{"effect_id":"se_1","type":"send_message","target_class":"self","status":"done","idempotency_key":"ct_demo_001:se_1"}}
{"v":2,"id":"e16","seq":16,"ts":1761000023600,"type":"TASK_STATE_CHANGE","task_id":"ct_demo_001","payload":{"from":"DELIVERING","to":"DELIVERED"}}
{"v":2,"id":"e17","seq":17,"ts":1761000023700,"type":"MEMORY_UPDATE","task_id":"ct_demo_001","payload":{"action":"distilled","memory_ref":"lesson:143","source":"user_correction","provenance":{"task_id":"ct_demo_001","turn_id":"t88"}}}
```

这份脚本同时是**回归夹具**：把它喂给 TaskEngine 回放，最终状态必须是 `DELIVERED`、
`effect_log` 恰好一条。同一份 JSON 既驱动动画，又驱动测试——文档和动画不会各讲各的。

## 6 权限与沙箱（M1 的硬前置）

### 6.1 为什么这章不能延后

v0.1 把权限留到了 §7，但 M1 第一步就要把 Worker 拉起来干活。现状是：

| 事实 | 位置 | 后果 |
| --- | --- | --- |
| Worker 子进程继承**整个**宿主环境变量 | `server/assistant/adapters/cli_subproc.py:50` `env = dict(os.environ)` | `LLM_API_KEY` 等凭据直接进了不可信进程 |
| sidecar 继承 `os.environ` + `extra_env`，后者含宿主只读令牌 | `server/assistant/durable_supervisor.py:68-72`；令牌生成于 `server/app.py:1442-1444` | 令牌只在 `extra_env` 里，**宿主拉起的 `cli_subproc` Worker 拿不到**（已核：`os.environ` 里没有它）；但 sidecar 拿得到，且 sidecar 是否再下发给它自己的工具进程**未验证**——按「会下发」防御 |
| 生产 Worker 跑在 `danger-full-access` | `config/agents_roster.yaml:150-163`、`docs/DECISION_TRAIL_20261017.md` D-13/D-22 | 对整机可写，隔离只有「工作目录约定」 |
| 验收命令只做**黑名单**（绝对路径、`..`、少量危险词） | `server/contract/lint.py:143-201` | `python -c "import shutil; shutil.rmtree(...)"` 能过 lint |

这不是「以后再说」的问题：**没有权限层的 Verifier 是一台以用户身份运行的任意代码执行器**。

### 6.2 权限等级（L0–L6）

`TaskContract.permissions_required` 的取值域就在这张表。G6 从「非空字符串」升级为「必须在本表内」。

| 等级 | 能力 | 举例 | 默认 |
| --- | --- | --- | --- |
| `L0` | 只读工作区 | 读文件、列目录、读已有笔记 | 默认授予 |
| `L1` | 只读工作区 + 只读记忆 | `search_memory`、`read_note` | 默认授予 |
| `L2` | 写工作区内（新建） | 生成报告、导出 CSV | 默认授予 |
| `L3` | 写工作区内（覆盖/删除） | 更新原表、删临时文件 | 需声明 + 备份 |
| `L4` | 出站网络（域名白名单） | 抓网页、查资料 | 按任务授予 |
| `L5` | 对外发消息 / 调外部写接口 | Webhook、邮件、IM | 每次确认 |
| `L6` | 花钱 / 安装 / 工作区外写入 | 充值、`pip install`、写 `~/` | 默认**不可授予**，需人工逐次批准 |

**等级与风险的关系**（接到 §3.4 的规则表上）：`permissions_required` 里的最高等级，本身就是 `risk` 的下界——
`L5`/`L6` 出现在契约里，`risk` 不得低于 `high`。这条是硬断言，不是建议：风险规则算漏了，权限表兜住。

### 6.3 三层防护（照抄 OpenAI/Modal 已被公开的做法，不自己发明）

**第 1 层：工具输入层（Harness 决定）**

- 参数 Schema 校验（已有：`server/assistant/loop.py` 的 validate）。
- 路径规范化：`realpath` 之后再判是否在工作区根内；**不只看字符串**（软链、`..`、盘符大小写、8.3 短名）。
- 命令不是跑正则：`exit_code_zero` 的 `cmd` 必须命中**白名单**（`pytest`、`ruff`、`tsc`、`node scripts/x.mjs` 这类），
  白名单在 `config/contract.yaml` 里配，不在代码里写死。今天的黑名单保留，作为第二道。

**第 2 层：OS 强制层（内核执行）**

| 平台 | 机制 | 现状 |
| --- | --- | --- |
| Linux | Landlock + seccomp（必要时 Bubblewrap）；只读根、可写子目录、无网络或白名单 | 未实现 |
| macOS | Seatbelt（`sandbox-exec`） | 未实现 |
| Windows | 受限令牌 + ACL + 防火墙规则；低权限账户运行 Worker | **未实现，且这是本机开发平台** |

Windows 这条要先做，因为开发和演示都在这台机器上。最小可行：为 Worker 建一个低权限本地账户，
工作区目录只对该账户授权，其余目录拒绝；网络出站走 §6.5 的代理。

**第 3 层：副作用审计层（证据）**

- 派发前后各做一次工作区清单（路径 + 大小 + 哈希），见 4.8。
- 差集里出现契约未声明的路径 → `E_EFFECT` → `FAILED`。
- `exit_code=0` **不等于**目标达成（PDF 报告第 4.2 节同一结论）：只证明「进程正常退出」。

### 6.4 凭据边界：Worker 不该拿到任何长期密钥

现在 Worker 能拿到宿主所有凭据。目标形态：

```
Worker（不可信） ── short-lived capability token ──► 本地网关 15722 ──► 上游模型
                  （按单签发，只够这一次任务）        （持有真实上游 key）

Worker（不可信） ── 出站请求 ──► Egress Proxy ──► 目标域名（凭据在代理侧按域名注入）
```

落地清单（按依赖排序）：

1. `cli_subproc.prepare_context` 不再 `dict(os.environ)`，改为**白名单构造**：
   只传 `PATH`、`TEMP`、`AQI_*` 与花名册里显式列出的变量。这一条改完就能挡住 90% 的凭据外泄。
2. 15722 网关加**按单令牌**：`AQI_GATEWAY_TOKEN`，签发给某一张 commission，任务结束即失效。
   今天的 15722 只靠「绑 127.0.0.1」当边界——**loopback 不是能力边界**，本机任何进程（包括浏览器里的
   恶意页面发起的 DNS rebinding、任何 CLI）都能用它，等于把上游 key 借给全机。
3. `AQI_WORKSPACE_ROOT` 从「约定」升级为「强制」：适配器启动前 `realpath` 校验，越界直接拒绝派单。
4. `XIAOXI_HOST_TOKEN` 的边界要写死：sidecar 需要它，Worker 不需要。今天宿主→`cli_subproc` 这条**没有泄漏**（令牌不在 `os.environ`），但 sidecar→其工具进程这条**没验证过**，应按「不传」设计并在 sidecar 侧显式剔除。

> **`/internal/telemetry_proxy` 的漏洞（真实存在，已核）**：该路由只做 Origin 校验
> （`server/assistant/executive_api.py:411-413`），**没有 Origin 头就放行**，
> 然后拿 `settings.llm_api_key` 去转发任意 `path`（`:420-426`）。
> 本机任意进程一条 `curl` 就能白嫖用户的模型额度。
> 修法：改为**必须**带本进程生成的令牌（复用 `set_host_token` 那套 `hmac.compare_digest`），
> 或直接绑到 127.0.0.1 之外的抽象命名空间。**这一条建议立刻单独修，不等 v2。**

### 6.5 出站与网络

- `L4` 任务的出站必须经代理：代理按「域名白名单 + 按单授权」放行，并记 `egress_denied`。
- 抓回来的网页内容一律当**不可信数据**：进 prompt 时加边界标记（现有记忆注入已有这个先例：
  `server/memory/retriever.py` 的「不可信记忆数据」前缀），并且**不得**从内容里提取指令执行。
- Prompt injection 的防线在**权限**，不在提示词：即使模型被骗，它也没有超出 `permissions_required` 的能力。

### 6.6 BLOCKED 也是授权出口

`BLOCKED.reason` 里 `need_authorization` 是独立一类：不是「重试超限」，而是「这一步要的能力没给」。
两者不能混：前者升级权限就能继续，后者再试一百次也一样。
对应 `blocked_kind` 的映射：`need_clarification → clarification`，其余四类 → `approval`。

## 7 可观测性、网关层与系统自身的验收

### 7.1 事件流即 trace

- 每条事件追加写 `data/events/<yyyy-mm-dd>.jsonl`，一行一条，与 §5.1 的信封同形。
- 同一条 trace 的键是 `task_id`；跨任务的因果靠 `parent_task_id`（子任务）。
- **回放**：把 JSONL 按 `seq` 喂回 TaskEngine，最终状态必须与线上一致。这是 P9「一切可回放」的可执行判据。
- 存储纪律：这份 JSONL 只存**状态与证据**，不存 prompt 正文、不存凭据（与 `agent_telemetry_spans`
  的脱敏规则一致：`server/assistant/telemetry.py` 的 `error_snippet` 会剥掉 token/password 类文本）。

### 7.2 指标（每一条都要能从 JSONL 算出来，不许手填）

| 指标 | 定义 | 为什么重要 |
| --- | --- | --- |
| `task_first_pass_rate` | 首次验收即通过的任务 / 总任务 | 「一次做对」的能力，比「最终能做成」更能反映体验 |
| `verifier_false_pass_rate` | 用户打回但 Verifier 判过的比例 | Verifier 的上界；这是记忆飞轮最大的污染源 |
| `verifier_false_block_rate` | Verifier 判不过但用户认为没问题的比例 | 打回太多会让人不再信任验收 |
| `clarification_rounds_avg` | 每单平均追问轮数 | 语音场景的耐心上限很低 |
| `slot_fill_precision` | 静默补全的槽位中被用户改掉的比例（1 - 错误率） | 校准 §3.5 的 0.85 / 0.70 |
| `step_unknown_ratio` | 进入 `UNKNOWN` 的 Step / 已派发 Step | 高 = 幂等与对账没做好 |
| `effect_duplicate_count` | 同一 `idempotency_key` 产生第二个副作用的次数 | **必须恒为 0**，非 0 即事故 |
| `illegal_transition_count` | 被拒绝的非法迁移次数 | 非 0 说明调用方在绕过状态机 |
| `task_wall_p95` / `cost_per_verified_task` | 墙钟 P95；总成本 ÷ 经验证成功任务 | 只对「真正做成的」算成本 |

**反例纪律**：任何指标为 0 时，先确认分母不是 0。今天的台账就有这个坑——
`TelemetrySummary` 在没有 span 时会返回 `calls=0`、`success_rate=null`，前台据此闭嘴而不是报「很健康」
（PROTOCOL §20）。同一套纪律搬到这里。

> **两个指标依赖一个不存在的动作（2026-10-22，UI/UX 设计师核对 §7.2 与 §5.2/§5.3 时发现）**：
> `verifier_false_pass_rate` 与 `verifier_false_block_rate` 都需要「**用户对 Verifier 的判定有异议**」这个数据，
> 但 **§5.2 的九个事件里没有任何一个记录它，§5.3 的六条命令里也没有对应动作**。
> 现成的两个迁移都不是它：`VERIFYING → EXECUTING`（T16）是**自动**打回（同一签名换一种错法），
> `VERIFYING → BLOCKED`（T17）是**转人工**（含 human 标准），两者都不表达「用户说 Verifier 判错了」。
>
> **所以这两个指标当前算不出来，而且用户缺少打回验收的入口**——不是「打回太多」，
> 是「无法打回」。补齐需要新增事件或命令，属协议变更，**必须 `[PROTOCOL_REQUEST]`**。
>
> **在那之前**：这两个指标标「未实现」，**不许手填，也不许用 `MEMORY_UPDATE` 的 `corrected` 替代**
> ——那是记忆纠正，不是验收纠正，两者语义不同（一个改置信度，一个改判定结论）。

### 7.3 网关层：需不需要，需要哪一层

审阅意见问「要不要网关与认证」。结论分三段：

1. **现在已经有三个网关，只是没被当成一层管**：
   - `server/app.py`（8000，`/ws` + REST）；
   - `server/gateway/anthropic_proxy.py`（15722，模型协议翻译）；
   - `server/internal/telemetry_proxy`（8000 上的旁路代理）。
   它们各自做各自的事，**没有统一的路由/认证/限流**。这正是要补的。

2. **M1 不需要新进程，只需要一条统一入口规则**：

   | 入站 | 认证方式 | 现状 |
   | --- | --- | --- |
   | 浏览器 `/ws`、`/api/*` | 同源（netloc 比对）+ 无 Origin 放行 | 已有（`server/app.py:1048-1072`） |
   | 本机 CLI / 脚本 | **无**（靠 loopback） | **缺口**；建议加 `X-Xiaoxi-Client` 令牌，与浏览器区分 |
   | Worker 子进程 → 15722 | **无** | **缺口**；按单令牌（6.4） |
   | sidecar → `/internal/durable` | Bearer + `compare_digest` | 已有（`server/assistant/durable_api.py:37-43`） |
   | Worker → `/internal/telemetry_proxy` | **无** | **缺口**（6.4 末） |

3. **M3 才需要独立网关进程**（多客户端、多设备、桌面壳）。到那时把三类职责分开：
   连接映射（谁订阅什么）、事件推送（seq / 补齐）、双向控制（命令幂等）。
   与 PDF 报告 §3.3 的四项职责一一对应。**M1/M2 不要提前造**：单用户单机，多一跳只会多一处失败点。

### 7.4 系统自身的验收（不是任务的验收）

v0.1 定义了「怎么验收一个任务」，没定义「怎么验收这套系统」。这是审阅意见 P2 的最后一击，必须补。

**M1 出口判据（全部可执行，无主观项）**

| # | 判据 | 命令 / 证据 |
| --- | --- | --- |
| 1 | 契约层单测全绿（G1–G6、L1–L7、风险表、置信度四档、CsvRule 6 条） | `python scripts/test_contract.py` |
| 2 | 状态机属性测试全绿（§4.10 的 18 条 + 随机序列） | 新增 `scripts/test_task_fsm.py` |
| 3 | 事件脚本回放幂等 | 喂 §5.7 的 JSONL，最终 `DELIVERED`，`effect_log` 恰好一条 |
| 4 | 协议零漂移 | `python scripts/check_protocol.py` |
| 5 | 核心零回归 | `python scripts/test_core.py` |
| 6 | 前端零回归 | `npm test` |
| 7 | 离线评测全绿 | `python scripts/run_evals.py` |
| 8 | 模糊指令评测集（§8）达标 | 见 §8.3 的阈值 |

> 这些命令的权威定义以 `docs/TESTING.md` 为准；本表只声明 M1 出口**必须**包括它们。
> 不要把某次跑出来的数字当现状写进文档（`AGENTS.md` 的三分法）。

## 8 评测集

### 8.1 三类样本，与现有 `evals/` 同一形态

现有评测是「一个 YAML 一个用例，offline 层无网络、live 层需 Key」
（`evals/cases/*.yaml`、`scripts/run_evals.py`）。v2 的评测直接接进去，**不另造 runner**。

| 类 | 样本 | 层 | 判据 |
| --- | --- | --- | --- |
| A 类·模糊指令 | 「把昨天那个竞品表格更新一下发我」这类 20–30 条 | 多数 offline（Gate/契约可判定），少数 live | 槽位补全是否正确、追问是否只问关键那一句、契约是否成形 |
| B 类·验收 | 每个 Criterion 类型各若干，含「明显不达标」的反例 | 全 offline | 断言函数本身的假阳/假阴率 |
| C 类·风险 | 每类副作用至少一条，含「看似 low 实则 high」 | 全 offline | 高风险任务未经确认执行数 **必须为 0** |
| D 类·状态机 | §4.10 的 18 条 | offline | 迁移被拒/被允许与表一致 |

### 8.2 A 类样本的写法（示例）

```yaml
id: gate-ambiguous-competitor-table
title: 模糊指令「昨天那个竞品表格更新一下发我」应补全到可验收的契约
layer: offline
origin: 规范 §3.6 的完整示例
tags: [gate, slot-fill, risk]
setup:
  memories:
    - {kind: todo, subject: user, key: "eval:competitor-table",
       value: 竞品对比表在 ~/work/competitors/竞品对比.csv, source: stated, confidence: 0.91}
    - {kind: fact, subject: user, key: "eval:deliver-webhook",
       value: 发我 = 发到本地 Webhook, source: stated, confidence: 0.88}
turns:
  - 小汐，把昨天那个竞品表格更新一下发我
pass_rule: all
checks:
  - target: contract
    type: field_equals
    value: "risk.level=medium"          # 有副作用 + 不可撤回
  - target: contract
    type: field_equals
    value: "deliver_to.highlight=true"  # 副作用槽位即使 0.88 也要高亮
  - target: contract
    type: custom
    value: "evals.checks:every_criterion_has_kind_and_check"
  - target: confirm
    type: field_equals
    value: "auto_start_allowed=false"   # risk=medium 不走倒计时
status: active
```

`target: contract` / `target: confirm` 是新的判定目标（现在的 runner 只认 `reply` / `prompt`，
`scripts/run_evals.py:97-100`）。**加这两个 target 属于评测基础设施改动，不是协议改动**，
可以直接做；但它必须向后兼容（旧用例不写 target 时默认 `reply`）。

### 8.3 达标阈值（初始值，随评测集一起校准）

| 指标 | M1 目标 | 为什么是这个数 |
| --- | --- | --- |
| A 类槽位补全准确率 | ≥ 95%（静默档）/ ≥ 99%（高亮档，因为用户能看到） | 静默档用户看不见，错了无从发现 |
| A 类追问轮数均值 | ≤ 1.5 | 语音里超过 2 轮就像盘问 |
| B 类断言假阳率 | 0（断言说通过但人判不通过） | Verifier 的意义就在这里 |
| C 类越权执行数 | **0** | 不可妥协 |
| D 类非法迁移放行数 | **0** | 同上 |
| 一次通过率 | 仅记录，不设目标 | 它取决于 Worker 与任务难度，不是契约层的锅 |

**阈值校准**：跑 A 类时同时画「补全覆盖率 × 补全准确率」曲线，取 95% 准确率对应的最大覆盖率作为
`silent_fill_min`。这是 §3.5 承诺过的事，评测集一到位就做，别再用「拍的 0.85」。

## 9 里程碑与验收证据

### 9.1 M1（后端契约 + 验收闭环，不碰画面）

**范围**：§3（已完成）+ §4 + §5.1–5.3 + §6.3 第 1 层 + §7.1 + §8。

| 交付 | 落点 | 证据 |
| --- | --- | --- |
| TaskContract / lint / 风险 / 置信度 | `server/contract/`（已实现，**但尚未接入运行时**：`server/app.py` 对它有 0 处引用，今天只有 `scripts/test_contract.py` 在用） | `scripts/test_contract.py` |
| **把 IntentGate 接进 `_run_turn`**（替换 `app.py:736` 的正则路由） | `server/app.py`（Role 0） | 旧路由语料差分：既能命中今天能命中的，也能命中「帮我把…」这类今天静默的 |
| TaskEngine（双层状态机 + 迁移表） | 新包 `server/task/` | `scripts/test_task_fsm.py`（属性测试） |
| Verifier（确定性跑 + 语义 Judge 分离） | 复用 `server/assistant/evaluator.py` 的 L1 规则体系，**不新造** | 每条规则一个反例用例 |
| 交付与 `effect_log` | `server/task/delivery.py` | 幂等键重放测试（SM-11） |
| 权限等级与白名单 | `config/contract.yaml` + 适配器改造 | 越权用例全部被拒 |
| 事件写 JSONL | `server/task/events.py` | 回放一致 |

**M1 明确不做**（与 `docs/CONTRACT_V2_SCOPE.md` 的冻结清单一致，避免范围漂移）：
像素画面、多 Agent 并发调度优化、桌面壳、记忆晋升（动记忆核心要另立 ADR）。

### 9.2 M2（事件桥 + 最小像素工坊）

| 交付 | 证据 |
| --- | --- |
| v2 事件桥（WS + `after_seq` 补齐） | 断线重连测试：拔网线 10 秒后状态与快照一致 |
| 单房间 Demo：小汐 + 1 个工位 + 1 个质检台 | 浏览器回归脚本（`pixel-world/scripts/smoke.mjs` 同一形态） |
| 播 §5.7 的 JSONL 驱动动画 | 同一份脚本既驱动动画又跑断言 |
| 世界侧只读台账、不写 | 代码审查 + 网络抓包：世界只有 GET |

**M2 不许做的事**：世界内直接验收/打回（要动同源策略，另立 ADR）；
在画面里画假进度（`determinate` 必须真实）。

### 9.3 M3（记忆可视化 + 多工种）

- 档案室：三档来源（stated / inferred / uncertain）分架摆放，置信度可手动升降，
  每次变动发 `MEMORY_UPDATE` 并带 `provenance`。
- 多工种并发：`STEP_STATE_CHANGE` 同时驱动多个小人；世界必须能表达「三个人在一个铺子里」。
- 桌面常驻：此时再选桌面壳（Electron / Tauri），M1/M2 一律浏览器 + Vite。

### 9.4 证据的写法（防止「文档很漂亮，跑不起来」）

每个里程碑的验收记录必须包含：**命令 + 退出码 + 日期 + 提交号**。
例：`python scripts/test_contract.py` → 退出码 0（2026-10-21，HEAD `5f4d8f5`）。
不带提交号的测试数字在下一轮就没法追溯，这正是 `AGENTS.md` 三分法要防的事。

---

# 附录 A：三个追问的正面回答

## A.1 快慢环是不是「内外循环」机制？

**结构上不是嵌套的内/外循环，而是「一条五道闸的门禁链 + 两个执行体」。** 依据是
`server/app.py` 的 `_run_turn`（`:648` 起），一轮输入按顺序过：

| 顺序 | 闸 | 位置 | 判据 | 用模型 |
| --- | --- | --- | --- | --- |
| 1 | 编辑器观点会话 | `app.py:666` | 上一轮带 `editor_insight` | 是 |
| 2 | 语音体验 / 听说练习 | `app.py:697`、`:715` | 会话开关状态 | 是 |
| 3 | 台前台账路由 | `app.py:736` → `orchestrator.executive_route` | 5 条台账正则（派单/进度/战况/验收/打回）+ 2 条视图领航（星图/笔记）+ 台账状态非空 | **否** |
| 4 | 秘书 AgentLoop | `app.py:779` → `assistant.should_route` | 正则 + 会话状态 | 是 |
| 5 | 普通对话 | 兜底 | — | 是 |

关键事实：

1. **「过滤」确实存在，但不是「值得用模型才唤醒慢环」**。慢环的唤醒条件写在
   `orchestrator.py:60-65` 的 `_DELEGATION` 正则里：命中「去把/安排人/让她去…」这类句式的委托意图，
   **并且**台账可用。它是**规则判定**，不是模型判断「这件事值不值得动脑」。
   所以：普通闲聊永远不会唤醒慢环，而「帮我把便签整理成三版」这种**明显的活**也进不去
   （`docs/ADR_AGENT_BRIDGE.md:157` 记的「同义改写静默不建单」就是这个）。
2. **快环不是「小模型」，是「零模型」**。第 3 道闸的回答全部来自配置样本与真实台账，
   不发第二次模型请求（`app.py:734-735` 的注释即此意）。所以叫它「快系统」比「快模型」准确。
3. **两层之间不是循环，是三条单向通道**：快环派单后 `conn.spawn` 起后台任务（`app.py:980`），
   慢环完成后经 `commission_update` 广播回来（`app.py:1005`），用户验收/打回再经第 3 道闸回到慢环。
   **没有「慢环反问快环、快环再喂慢环」的回路**——今天所有追问都是慢环自己转 `blocked` 等用户。

**v2 要不要把它做成闭环？** 要，但不是「内外循环」，而是**把第 3 道闸从正则升级成 IntentGate**：

```
话语 ──► IntentGate ──► 需要澄清 → 追问（快环说一句，不建单）
              │                    └──► 用户答完 ──► 回到 IntentGate（这就是唯一的环）
              └──► 契约 ready ──► TaskEngine（慢环）
```

这个「追问 → 再判定」的环是**单层**的，出口只有两个：契约 ready 或放弃。
把它叫作「外循环套内循环」会误导实现——真的做成嵌套循环，就会出现「慢环里再开一个快环」的怪结构，
而正确做法是**一个 Gate 反复调用**，这正是 §4.4 的 T1（`CLARIFYING → CLARIFYING`）。

## A.2 需不需要路由与认证的网关层？

需要，但不是现在开新进程。见 §7.3。三句话总结：

- **路由**：已有，但是三套各自为政（8000 / 15722 / internal）。
- **认证**：浏览器侧有；**本机进程侧等于没有**，而 Worker 恰恰是本机进程。
- **网关进程**：M3 再拆。M1 只补「按单令牌」与「/internal 必须带令牌」两条。

## A.3 最小权限与沙箱是怎么考虑的？

见 §6。一句话：**今天是「约定式隔离」，需要升级到「能力式隔离」**。
最小改动、最大收益的三步：

1. Worker 环境变量白名单（`cli_subproc.py:50`）——挡住凭据外泄，半天工作量。
2. `/internal/telemetry_proxy` 加令牌——挡住本机白嫖模型额度，一小时。
3. `AQI_WORKSPACE_ROOT` 从约定变强制（`realpath` 校验）——挡住越界写，半天。

OS 级沙箱（Landlock / Seatbelt / 受限令牌）排在 M2，因为它需要平台适配，
但不做的话，前三条只是「让 Worker 不方便作恶」，不是「让 Worker 不能作恶」。

---

# 附录 B：对参考报告（OpenAI Agent Runtime & Sandbox 深度解读）的核查

## B.1 报告本身的可信度

报告的可信度分级（O/T/M/D）做得比多数二手解读好，且明确区分「官方事实」与「本报告设计」。
**它没有把 Modal 的实践说成 OpenAI 的实现**（§13 明确列为「目前不能确认」），这一点是对的，
应当保留这种标注习惯。

## B.2 需要修正/补充的地方

| # | 问题 | 说明 | 对本项目的影响 |
| --- | --- | --- | --- |
| 1 | 「两层状态机」把 Tool Execution 画成 7 态链（CREATED→…→VERIFIED），但它是**线性**的 | 实际工具执行会出现「已派发但结果未知」这种分叉，不是一条直线能走完的。报告自己在 §10.2 承认了 No-Replay，状态图却没画 `UNKNOWN` | 我们的 §4.5 把 `DISPATCHED` 与 `RUNNING` 分开，并显式加了 `UNKNOWN` 与两条对账出口 |
| 2 | 指标里缺「误放行 / 误拦截」 | 报告的可观测性全是**执行层**指标（sandbox 启动、dispatch 延迟），没有**判定层**指标 | 直接补 `verifier_false_pass_rate` / `verifier_false_block_rate`（§7.2）。对我们是这两个最要命，因为记忆飞轮会放大误放行 |
| 3 | 「Minecraft 作为统一 Benchmark」对本项目不适用 | 那是「世界状态可查询」的完美环境；我们的任务是文件与网页，**没有 oracle** | 换成 §8 的四类样本，其中 B/C/D 三类是确定性的，A 类是唯一需要 live 的 |
| 4 | Sidecar 的 p50 数字（0.58ms / 2.6ms）不可外推 | 报告已标注是厂商披露，但仍有被读者当成承诺的风险 | 我们不需要这些数字：本地单机通信的延迟不是瓶颈，**能力边界**才是 |
| 5 | 报告没回答「单用户的本地 Agent 要不要这么重」 | 它的参考架构是为多租户设计的（Tenant、Quota、Worker Fleet） | 我们的结论相反：**M1/M2 不要 Scheduler**。单用户本机，台账 + 一个后台任务集合就够（现状即如此，`app.py:980` 的 `conn.spawn`） |
| 6 | 「Event Store 不只存自然语言聊天」这条应更强调 | 报告列在表格里，但没说清「聊天气泡不能当审计」 | 我们的 §7.1 把它变成硬规则：JSONL 只存状态与证据，不存 prompt 正文 |

## B.3 报告直接引发的机制修改

| 报告结论 | 落到本规范 |
| --- | --- |
| 派发后不得盲目重放 | §4.8 的判定树 + `UNKNOWN` 两条出口 |
| `exit_code=0 ≠ 业务完成` | §6.3 第 3 层 + §4.6 的 `E_CRITERION` |
| 命令状态要带 `dispatch_receipt` / 幂等键 / 观测 / 验证结果 | §4.8 的对账表 + §5.2 的 `TOOL_EXECUTION` 载荷 + `effect_log` |
| 工具输入层不能只跑正则 | §6.3 第 1 层：命令白名单 + `realpath` |
| 凭据不进不可信环境 | §6.4 的四条落地清单（含今天就能改的三条） |
| 分离三个状态（Run / Session / Workspace） | §4.3 的投影铁律 + §5.4 的「快照用 REST」 |

---

# 附录 C：六张沙盘截图的审阅

截图反映的是一个**线性五阶段**叙事，与 §4 的双层并发结构对不上。逐条：

| # | 问题 | 具体 | 改法 |
| --- | --- | --- | --- |
| 1 | 没有 BLOCKED 分支 | 开关只有「通过 / 打回自愈」。重试超限、需授权、结果未知这三条路在图上不存在，而它们是**最常发生**的 | 加第三条分支，并在验收台旁单画一个「请示台」（对应 `ASK_USER`） |
| 2 | 事件总线只从 Verifier 出来 | 图里 `Verifier_Gate → Pixel_EventBus` 是一条单向边，但 `VOICE_STATE`、`TASK_STATE_CHANGE` 都来自别的模块 | 把总线画成**横切所有后端模块的一条竖带**，每个模块向下发事件 |
| 3 | `Task_Contract` 被画成进程节点 | 它是**数据**（一份契约），与 `Async_Orchestrator` 这种进程同级摆放，会让人以为它也会「执行」 | 改成横跨的「契约卷轴」形态，或放到 Gate 内部作为产出物 |
| 4 | `Sync` 边语义不明 | `System1_Voice --Sync--> Tiered_Memory` 只标了「同步」，看不出是读还是写、是同步还是异步 | 拆成 `read →`（补槽）与 `write ←`（记忆变更），并标时刻（派单前 / 验收后） |
| 5 | 五阶段是线性播放 | 三个阶段（并发、验收、交付）在真实系统里是**同时发生**的：一个 Task 验收时另一个在派单 | 播放器改成「时间轴 + 泳道」，不是「上一页/下一页」 |
| 6 | 缺「用户」这个参与者 | 图上没有用户的入口与出口，追问、确认、打回都在图外 | 加一个「神谕祭坛」节点，`ASK_USER` / `CONFIRM_CONTRACT` 都连到它 |
| 7 | 缺记忆写回的回边 | `Tiered_Memory` 只有一条虚线连 `Task_Contract`，看不出验收后写回什么 | 按 §5.6 画两条：`distilled`（经验，虚线）与 `promoted/confirmed`（事实，实线），**粗细不同** |

**一句话结论**：沙盘要先按 §5.7 的事件脚本重做，图对不对由脚本跑出来决定，而不是由人画出来决定。
脚本能跑通、六个阶段都能被脚本复现，图就自然对了。

---

# 附录 D：初版《AI 原生像素个人助理世界架构设计》回顾

初版（1095 行）写于像素世界还没落地时，视角是「先造一个世界」。它有四处洞见仍然成立，
三处判断已被现实推翻，还有两处我们至今没做。**兼听则明的价值主要在最后两栏。**

## D.1 至今成立、且本规范沿用的

| 初版主张 | 出处 | 现状 |
| --- | --- | --- |
| 「盒子里的世界」= Agent 能力的具象投影，不是套皮 | 第 1 节 | 已是 `pixel-world/README.md` 的第一条 |
| 用户是「神」，指令是神谕，角色对神谕积极尊重 | 第 1 节 | 已实现（行礼 → 进铺子） |
| 双模心智：无任务时本地逻辑跑日常，有指令时事件中断 | 第 2 节 | 已实现（`routines.ts` + `simulation.ts`），且**不花 token** |
| 造物主不是画师，是城市规划师；三层约束（网格规格 / 预制件 / 进化投射） | 第 3 节 | 部分实现（`blueprints.ts` / `prefabs.json`），进化投射未做 |
| 工坊 = 工具调用的物理插槽（`Workstation.supported_tool`） | 第 3 节 | 已实现为 `boundTask`，但**一个工位只接一类活**，还不是「按工具名匹配」 |
| 拟音（哇啦哇啦）替代语音播报 | 第 1 节 | 已实现（`src/audio/animalese.ts`），这条是对的：语音播报确实与像素风格冲突 |

## D.2 已被推翻的判断

| 初版主张 | 为什么推翻 | 依据 |
| --- | --- | --- |
| 「坚定采用 Bevy (Rust)」 | 实际采用 Phaser 3 + Vite + TypeScript | `docs/PIXEL_WORLD_RESEARCH.md` 头部标注 + `docs/DECISION_TRAIL_20261019.md` D1 |
| 「造物主组件」作为核心模块 | 世界是**固定蓝图 + 少量摆件**，不是运行时生成建筑。真正的「造物」是切片 2 的造物模式，不是 M1 的东西 | `docs/ADR_WORLD_ALIVE.md` 的切片顺序 |
| 用 ECS 组织「大量小人状态机」 | 目前只有**一个人**（莞尔）在动，ECS 的收益为零，复杂度全在状态同步 | `pixel-world/src/world/simulation.ts` |

## D.3 初版有、我们至今没做（值得捡回来的）

| 缺口 | 初版怎么写的 | 为什么值得做 |
| --- | --- | --- |
| **信仰/资源结算** | 「任务完成得资源，资源改变世界」+ 第 6 阶段的「经济闭环」 | 已实现一个**极简版**：每单 1 点、满 3 点落一个摆件（`src/world/faith.ts`，不发行货币）。这个取舍比初版更好——**不可花的累积**避免通胀与最优解。但「资源改世界」的**玩家能动性**还没有：摆件是自动落的 |
| **世界法则通道**（用户说「下雨」→ 世界下雨） | 第 2 节：把意图分成「现实助理」与「世界法则」两条通道 | 天气系统有了（`weather.ts`），Mock 路也有入口（`pixel-world/src/world/oracle.ts` 的 `RAIN_WORDS`），但**server 的 live 路没有**——同一句话在 `?ledger=on` 下不会下雨。与 `ADR_AGENT_BRIDGE.md` §4.4.1 记的「同义句 live 静默 / Mock 接走」是同一个结构性缺口。补齐方式是把 `weather` 作为一种**无副作用的世界命令**接进 IntentGate 的 `rejected`→旁路（不进任务状态机，不需要契约） |
| **Y-Sort 遮挡** | 第 3 节专章 | 已解决（`building-depth.test.ts`），但**人走到建筑门口时的层级切换**在截图里看不出处理过，值得复核 |
| **鬼打墙的「滑稽故障」表现** | 第 3 节 `Overheating` 状态 | 熔断在台账侧已实现（`spinning_guard`），但世界侧只表现为 `blocked`，没有「机器冒烟打嗝」这种**让人一眼看懂**的表达 |

## D.4 初版与本规范的一处根本分歧

初版认为世界的价值是「**可读的空间形态**」——把 swimlane 卡片变成铺子，让人一眼看见谁在干活。
本规范认为世界的价值是「**可信的状态投影**」——画面上的每一帧都必须能追到一个真实字段。

两者不冲突，但**优先级不同**，而优先级决定取舍：

- 按初版，会优先做「多工种并发动画」（好看、直观）；
- 按本规范，会优先做「BLOCKED 分支 + 取消/失败可辨识」（不好看，但不说谎）。

**建议后者优先**：一个会撒谎的画面，比一排枯燥的卡片更糟。
`pixel-world/README.md` 已经把「世界能不能不撒谎」列为四条验收之一，这条纪律应继续压过「好看」。
