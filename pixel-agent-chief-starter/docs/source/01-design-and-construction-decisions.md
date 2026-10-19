# AI 原生像素个人助理世界 —— 设计讨论与施工决策文档

> 版本：v1.0（对话整理）
> 用途：项目主设计文档 / 编码 Agent 交接基线 / 决策追溯
> 说明：本文档汇总了从架构构思到样品范围界定的完整讨论，包含技术选型、组件设计、美术规范、LLM 分工与施工纪律。任何接手施工的 Agent 应先通读本文档，再读 `PROJECT_PROGRESS.md` 对齐进度。

---

## 目录

1. [项目愿景与核心隐喻](#一项目愿景与核心隐喻)
2. [引擎选型：从 Bevy 到 Phaser 3 的决策过程](#二引擎选型从-bevy-到-phaser-3-的决策过程)
3. [最终技术栈与配套层](#三最终技术栈与配套层)
4. [开发重点：由难到易的排序](#四开发重点由难到易的排序)
5. [像素美术风格：五个维度的关键点与实现](#五像素美术风格五个维度的关键点与实现)
6. [程序与 LLM 的能力分工](#六程序与-llm-的能力分工)
7. [世界塑造组件体系](#七世界塑造组件体系)
8. [样品范围界定与流程纪律](#八样品范围界定与流程纪律)
9. [施工追踪模板](#九施工追踪模板)

---

## 一、项目愿景与核心隐喻

### 1.1 一句话定位

一个 **AI Native 的像素风"盒子世界"个人助理**：用户是游戏人物眼中的"神"，Agent 指令是"神谕"，现实世界中的任务执行同步具身化为游戏内的建筑运转与小人劳作。

### 1.2 三个融合来源

| 来源 | 借鉴点 |
|---|---|
| 斯坦福 AI 小镇（Generative Agents） | 自治生态：记忆流、反思、规划、行为执行 |
| 《黑与白》（Black & White） | "神谕"交互隐喻：用户不直接操控，通过指令影响世界 |
| 桌面挂机种田游戏（如《Rusty's Retirement》） | 陪伴感：低打扰、可视化的后台进程 |

### 1.3 核心设计原则

- **双模驱动**：无任务时世界自由生活；有神谕时高优中断、积极响应。
- **双通道解耦**：现实助理通道（调动工具干活）与世界法则通道（改变天气/环境）分离。
- **造物主约束生成**：世界规格受约束，不杂乱也不简陋。
- **程序持有事实，LLM 生成意义**：LLM 输出一律视为不可信提案，须经 Schema 与确定性校验。

### 1.4 已确认的关键取舍

1. **不做主动键鼠玩法**：用户只通过"神谕"输入影响世界。
2. **不做真人 TTS 语音**：用程序化像素拟音（"哇啦哇啦"）+ 世界内载体（报纸、小人汇报）代替。
3. **纯 2D 正交像素风**：关闭抗锯齿、整数缩放、像素完美优先于动态光照。
4. **99% 行为由本地规则驱动**：LLM 只在低频离散节点介入。

---

## 二、引擎选型：从 Bevy 到 Phaser 3 的决策过程

### 2.1 三个候选方案对比

| 维度 | Phaser 3 / PixiJS（Web/TS） | Bevy（Rust） | Godot 4.x |
|---|---|---|---|
| 开发调试摩擦力 | 极低，浏览器 F12 + HMR | 中高，Rust 编译期心智负担 | 低，但编辑器耦合深 |
| 与 Agent 后端通信 | 原生 WebSocket/JSON，零摩擦 | 需强类型反序列化桥接 | 需 WebSocket 节点桥接 |
| UI 扩展（报纸/面板） | DOM/CSS 叠层，降维打击 | 原生 UI 偏底层 | 内置 UI 良好 |
| 运行时动态改世界 | JS 对象/JSON 直接注入 | 需重新组织 ECS 实体 | 场景文件脚本生成，编辑器耦合深 |
| 像素基建（Auto-Tile/光照） | 需自建或插件 | 社区 Crate | 最强，但本项目用不到 |
| 常驻桌面资源 | Tauri 壳约 60–80MB | 25–45MB | 60–100MB |
| 迭代速度（单人+AI 辅助） | **最快** | 中 | 中 |

### 2.2 决策结论

**选择 Phaser 3 路线。** 理由：

- Phaser 的短板（无内置 ECS、无成熟 2D 光照、无 Auto-Tile）恰好可由**自研数据层 + 轻量插件**补上，成本远低于在 Bevy/Godot 中重建 Web UI 和 Agent 通信。
- 斯坦福 AI 小镇开源版即基于 Phaser，有成熟参考。
- 与外部 Python/Node Agent 的 WebSocket 通信是原生本能。

**保留退路**：ECS 设计思维在 Bevy 与 TypeScript 手写数据层之间 100% 互通，若 Rust 心智负担过重可无缝平移。

### 2.3 曾讨论的 Bevy 方案要点（归档参考）

若未来迁移 Bevy，核心设计已明确：

- **ECS 五概念**：Entity（ID）、Component（纯数据）、Resource（全局状态）、Event/Observer（神经网）、System（纯函数）。
- **动态进化**：给实体 `insert`/`remove` 组件即可改变行为，无需继承树。
- **Required Components**：`#[require(GridPos, BuildingState)]` 自动补全依赖组件。
- **Y-Sort 黄金法则**：`transform.z = 100.0 - (base_y * 0.01)`。
- **非阻塞桥接**：`tokio` + `crossbeam-channel`，主循环每帧 `try_recv()` 轮询。

---

## 三、最终技术栈与配套层

### 3.1 技术栈全景

| 层次 | 选型 | 理由与取舍 |
|---|---|---|
| 语言/构建 | **TypeScript + Vite** | 强类型约束、HMR、AI 生成代码友好 |
| 渲染设置 | `pixelArt: true`、`roundPixels: true`、`antialias: false`、整数 zoom | 像素完美是底线 |
| 静态底图 | **Tiled**（仅底图与 Zone 标注） | 运行时动态生成不能靠 Tiled |
| 运行时网格 | **自研 WorldGrid** | 造物主需运行时占用/通行/分区查询 |
| 建筑/装饰 | **Prefab JSON + Aseprite spritesheet** | 数据驱动、热加载、AI 可读 |
| 动画 | Phaser Animation + Aseprite 帧标签 | 成熟，不引入 Spine |
| 寻路 | **EasyStar.js** | 支持动态障碍更新，匹配造物主生成 |
| 逻辑架构 | **手写"数据组件 + 系统函数"**（轻量 ECS 风格） | 避免重型 ECS 与 Phaser GameObject 生命周期冲突 |
| 状态/事件 | `Phaser.Events.EventEmitter` + 集中 `GameState` 单例 | 不用 Redux |
| 通信 | **原生 WebSocket + 消息队列 + 重连/心跳封装** | Agent 后端多为 Python/Node |
| 音频 | **Web Audio API 程序化合成** + Phaser Sound 切片 | 拟音需实时 pitch 偏移 |
| UI | **DOM/CSS 叠层**（报纸、设置、输入框）+ Phaser 内气泡/进度条 | 排版灵活 |
| 光照 | **先伪光照**（全局调色 + 发光贴图 + 混合模式），Light2D 留 P3 | 像素完美优先 |
| 桌面壳 | **Tauri**（后期） | 体积小 |
| 美术工具 | **Aseprite + 固定调色板 + 像素簇规范** | 美术纪律的基础 |

### 3.2 四个必须提前拍板的架构取舍

1. **像素完美 vs 动态光照**：先像素完美；光照用"全局环境色 + 局部发光贴图 + 乘法/叠加混合"模拟；实时 Light2D 作为 P3 再评估。
2. **LLM 生成 vs 确定性布局**：LLM 只输出**高层意图**（Zone、风格、建筑清单），具体坐标由确定性空间分配器计算。
3. **重型 ECS vs 手写数据层**：不引入 ECS 库，用 `Component` 接口 + `System` 纯函数 + `WorldGrid` 资源单例。
4. **实时 LLM vs 本地规则**：99% 走位、动画、日常行为由本地状态机/效用函数驱动；LLM 只在"神谕解析、日终反思、偶遇闲聊"三个低频点介入。

---

## 四、开发重点：由难到易的排序

> 排序依据：**技术不确定性 × 架构影响面**。越靠前越应先做 Spike 验证。

### 第一档：架构级难点（必须 Spike 验证）

#### 难点 1：造物主的"约束生成"

- **矛盾**：LLM 输出不可靠 vs 像素世界需要严格网格规则。
- **策略**：三层约束法落地为代码——`ZoneType` 硬约束 → `footprint + clearMargin` 硬约束 → **布局评分器**（连通性、留白率、分区纯度、主路可达性）软约束。LLM 只输出 `CreatorCommand` JSON，坐标由 `findBestSlot()` 确定性分配；失败走模板回退。
- **验证**：20 条不同 `CreatorCommand`，100% 无重叠、主路连通、分区纯度 > 90%。

#### 难点 2：神谕桥接的分布式状态一致性

- **策略**：全局唯一 `task_id` + 幂等消息 + 明确任务状态机（Assigned → Saluting → Moving → Working → Delivering → Done/Failed/Cancelled）。游戏侧维护任务超时回收器。
- **验证**：Mock 脚本随机注入异常（乱序、重复、超时、取消、断线），状态 100% 可恢复无卡死。

#### 难点 3：双模心智的抢占、恢复与并发

- **策略**：**行为栈（Behavior Stack）** 而非单一状态机——日常行为栈底，神谕压栈，完成后弹栈；只在"行为切换边界"允许抢占，移动中设 `pendingInterrupt`。
- **验证**：3 神谕 + 1 取消 + 下雨避雨，无抖动无瞬移。

### 第二档：集成级难点

#### 难点 4：像素美术一致性与垂直切片

- **策略**：Phase 1 并行做垂直切片（地面 + 1 建筑 + 1 角色 + 1 植物 + 1 道具 + 1 光源）；产出 1 页视觉规范；按 P0→P4 优先级验收。
- **验证**：原生分辨率 3 秒内指出主角/路径/交互点；400% 放大无接缝错位。

#### 难点 5：光照/天气与像素风格融合

- **策略**：分层实现——环境色全局调色、局部光低分辨率发光贴图、粒子低分辨率纹理放大；Light2D 放 P3。
- **验证**：白天/夜晚/室内外三态对比，暗场中角色轮廓仍可辨。

#### 难点 6：Y-Sort 与建筑分层遮挡

- **策略**：统一以**脚底 Y 坐标**排序（Phaser 用 `depth`）；建筑拆地基/主体/屋顶/前景四层；屋顶在小人进入范围时淡出。
- **验证**：小人绕建筑走一圈，遮挡连续无跳变。

### 第三档：实现级难点（有成熟方案）

| 难点 | 方案 |
|---|---|
| 动态障碍 A* 与多小人寻路 | EasyStar.js `updateGrid`；先做"允许重叠、后到者等待" |
| 工坊多状态动画与长短任务分级 | Phaser 粒子/动画成熟；<2 秒原地施法，长任务进驻工坊 |
| 程序化拟音与打字机同步 | Web Audio 合成 12 个基础音节 + 字符 Hash pitch |
| 造物主自动铺路与连通性 | 从门口 A* 到主路，用 `walkable`/`occupant` 约束 |

### 第四档：工程量（按 Phase 推进）

报纸/九宫格 UI、资源结算、网格绘制、基础场景、Mock 脚本、对话框/气泡/进度条。

### 建议的开发顺序

```
立即并行（2 条线）：
  A线 技术Spike（1~2周）
    ├─ Spike-1：造物主布局器（纯数据无渲染）
    ├─ Spike-2：神谕状态机 + Mock异常注入
    └─ Spike-3：行为栈原型（2个小人即可）
  B线 美术垂直切片
    └─ 1张代表场景 + 1页视觉规范 + A/B/C细节预算

Spike 通过后：
  Phase 1 改造：WorldGrid + PrefabRegistry + CreatorManager + 自动铺路
  Phase 2 改造：Mock 全异常路径跑通，再接真实 Agent WebSocket
  Phase 3：行为栈正式落地
  Phase 4~6：按原计划推进
```

---

## 五、像素美术风格：五个维度的关键点与实现

> **底线**：可读性 > 装饰性。原生分辨率下能一眼认出主角、路径、交互物、神谕状态。

### 5.1 形象（角色与建筑）

**关键点**：
- **剪影测试第一关**：纯黑轮廓 0.5 秒内能否辨出身份/职业。需要标志性外挂（帽/工具/披风）、左右不对称、肢体间负空间。
- **尺寸参考**：俯视角 RPG 角色约 16×32 px（瓦片 16）；本项目 32px 瓦片，角色可做 24×40 或 32×48。
- **建筑三层**：主轮廓 → 功能线索（门/招牌/烟囱/窗光）→ 使用痕迹（磨损/补丁/杂物）。
- **消除简陋感**：造物主生成时 `clearMargin` 周围自动撒 1×1 微型装饰。

**Phaser 实现**：
- 角色精灵 `setOrigin(0.5, 1)`（脚底原点，服务 Y-Sort）。
- 建筑拆地基/主体/屋顶/前景四层 sprite。
- 选择性描边（selout）：受光侧用暗部色，背光侧用更深色。

### 5.2 动作

**关键点**：
- **呼吸待机两帧就够**：第一帧正常，第二帧上半身整体下移 1–2px + 手动微调头发/肩/衣摆；眨眼拉到 8 帧节奏。
- **动作五段结构**：待机 → 蓄力 → 发力 → 惯性/收势 → 回收。
- **跑步循环 6 帧**：2 接触帧 + 2 通过帧 + 垂直弹跳。
- **表情极简**：16×16 下眼睛约 3px、嘴 1–2px；只做"脸部帧"叠加，不重画全身。

**Phaser 实现**：
- Aseprite 帧标签导出，按 tag 建 `idle`/`walk`/`salute`/`work`/`celebrate`/`panic`。
- 帧率单独控制：待机 4–6 fps，跑步 10–12 fps。
- 神谕打断用 `pendingInterrupt`，在可中断帧再切换。
- 工坊冒烟优先用粒子/建筑层帧动画，小人只循环 2–3 帧敲打动作。

### 5.3 配色

**关键点**：
- **4–16 色受限调色板**，每色有明确角色（轮廓/暗部/基色/亮部/强调色）。
- **色阶思维**：每类材质固定 3–4 级色阶，不跨材质混用。
- **像素簇方向**：木纹顺板材、石缝方向一致、金属高光集中；禁止单像素随机撒点。
- **游戏调色板与 UI 调色板分离**（UI 需独立高对比 token）。
- **区域配色**：3–5 主导色 + 1 稀缺强调色（只给焦点物）。

**Phaser 实现**：
- `Palette.ts` 定义语义 token：`ui.text.primary`、`world.focus` 等，禁止硬编码十六进制。
- 全局环境色用**全屏 Rectangle + multiply 混合**做统一压暗（伪光照第一层）。

### 5.4 动效

**关键点**：
- **环境动效低频、低对比、有相位差**：草叶短而轻、树冠慢而成片、水面不规则、烟雾上升扩散。
- **粒子六参数分级**：尺寸、密度、速度、寿命、发光强度、层级。
- **像素与光照冲突**：优先"低分辨率纹理放大 + 混合模式"的伪光照，保留硬边。

**Phaser 实现**：
- 环境动效用 Sprite 帧动画 + 极低帧率（水面 3 帧/4fps，火焰 4 帧/8fps）。
- 粒子纹理必须低分辨率（6×6 或 8×8）。
- 多发射器加 `timeScale` 或起始帧随机偏移实现相位差。

### 5.5 互动 UI

**关键点**：
- **世界内 UI 必须"长在像素世界里"**：九宫格边框、像素字体、有限 UI 色板。
- **UI 可读性四层系统**：字形清晰 → 局部对比 → 结构层级 → 上下文韧性。
- **按钮尺寸底线**：不小于 8×12 px。
- **神谕状态双通道表达**：头顶图标 + 气泡短词，不能只靠颜色。

**Phaser 实现**：
- 世界内 UI 用 Phaser `Container` 跟随实体。
- 报纸/大面板走 DOM/CSS 叠层 + NineSlice/border-image 像素九宫格。
- 像素字体（Press Start 2P 类），不 fallback 到通用等宽字体。

### 5.6 资料查找三层法

| 层次 | 入口 |
|---|---|
| **学规则** | Lospec 教程库、Pixel Joint 入门帖、Saint11（Celeste 美术）图解、LobeHub 像素技能文档 |
| **看案例** | Spriters Resource（原版 spritesheet）、itch.io 免费像素资产、Steam 录屏（看移动/遮挡/粒子） |
| **逐帧拆解** | Aseprite 逐帧对比关键帧与补间帧；Brandon James Greer、1 Bit Studio、Pixel Pete（YouTube）；中文 32comic、千穗 |

### 5.7 垂直切片验收

同时验证：角色剪影可辨 + 2 帧呼吸待机、工坊四层拆分 + 冒烟 + 窗光、地面无接缝 + 像素簇方向一致、气泡/进度条可读、白天/夜晚或下雨全局色调。**原生分辨率 3 秒内能指出主角、路径、当前神谕状态**，才写 `ART_SPEC.md`。

---

## 六、程序与 LLM 的能力分工

> **核心原则**：程序持有事实、约束和生命周期；LLM 只在"需要理解语义或生成意义"的离散节点被调用，输出必须经 Schema 校验后才能回写程序世界。
> 参考模式：概率核 + 确定性包络；Codex harness 原则"模型是决策者，不是系统所有者"。

### 6.1 世界运行规则

- **程序**：网格、寻路、物理、状态机、天气状态、行为栈。`GameState`/`WorldState` 是规范对象，单一来源、可校验、可回滚。
- **LLM**：只输出意图级指令（`CreatorCommand`、`WorldMagicCommand`、`OracleParse`），不输出坐标、不直接改变量。
- **边界**：程序决定"世界能不能这么变、变了物理上发生什么"；LLM 决定"往哪个方向变、用什么语义描述"。
- **变更管线**：`Plan – Diff – Validate – Apply`，LLM 输出严格 Schema 约束，失败走模板降级。

### 6.2 记忆

- **程序**：持久化、索引、分层召回。三层记忆树——Tier 1 短摘要常驻、Tier 2 详细摘要惰性、Tier 3 原始内容按需。召回模式：passive/active/both。
- **只存三类事件**：神谕事件、社交/反思事件、世界里程碑。日常走位和动画不进记忆。
- **LLM**：压缩为 `summary + trigger + tags`；日终反思聚合为 1–3 条"信念/观察"。
- **边界**：程序负责"记住什么、何时召回、怎么存"；LLM 负责"一句话是什么、何时想起、意味着什么"。

### 6.3 自成长

**变更管线**：
```
事件/资源阈值触发
  → LLM 读世界摘要 + 记忆摘要 + 资源账本
  → 输出 EvolutionProposal（JSON Schema 约束）
  → 程序可行性校验（Zone 空间、资源、前置建筑）
  → 通过 → Creator 执行 + 施工动画
  → 不通过 → 模板降级 或 挂起
```

- **程序**：触发条件（资源阈值/任务次数/时间周期）、可行性校验、执行与回滚。
- **LLM**：提案内容与叙事（升什么、叫什么、什么风格、伴随什么事件）。
- **边界**：程序是"规划局和银行"，LLM 是"建筑师和说书人"。

### 6.4 神谕反映

**第一阶段：Intent 解析（LLM，低频短输出）**
```json
{
  "channel": "task" | "world_magic" | "both",
  "task_type": "coding" | "research" | "writing",
  "target_role": "Coder" | "Scholar",
  "urgency": "normal" | "high",
  "world_magic": { "type": "rain", "params": {} } | null,
  "display_summary": "吾神命工坊炼制爬虫脚本"
}
```
失败回退 `channel: "task", task_type: "generic"`。

**第二阶段：反应回路（程序，确定性）**
- 广播 `OracleReceived` → 祭坛发光 → 匹配空闲小人 → 行为栈压入 `ActiveOracleTask` → 行礼 → 寻路 → 工坊 `Operating` → 完成 `Delivering` → 资源结算 → 弹栈恢复。

**LLM 仅在三个低频点介入**：意图解析、气泡台词、汇报叙事。

### 6.5 汇总表

| 维度 | 程序负责 | LLM 负责 | 关键约束 |
|---|---|---|---|
| 世界运行规则 | 网格、寻路、物理、状态机、天气、行为栈 | `CreatorCommand`/`WorldMagicCommand` 意图 JSON | Schema 校验；失败降级；不直接改 `GameState` |
| 记忆 | 存储、索引、分层召回、TTL | `summary + trigger + tags`；日终反思 | 只存三类事件；Tier 分层 |
| 自成长 | 触发条件、可行性校验、资源扣减、执行回滚 | `EvolutionProposal`（升什么/叫什么/叙事） | 必过空间/资源/前置校验；不过则模板回退 |
| 神谕反映 | 事件广播、角色匹配、中断、动画、进度、结算、恢复 | `OracleIntent` + 气泡台词 + 汇报文字 | 解析失败走 generic；回路不依赖 LLM 在线 |

**贯穿纪律**：LLM 每次输出都是"不可信提案"——先过 Schema，再过确定性校验，失败有降级。程序世界不等待 LLM。

---

## 七、世界塑造组件体系

> 造物主解决"世界长什么样、空间怎么被占用"。以下八个组件与它同级，共同构成世界塑造层。
> 统一前提：**只通过全局事件总线通信，不互相直接调用；所有状态变更走 `WorldState` 快照。**

### 7.1 总览

| 组件 | 塑造维度 | 一句话职责 | 与造物主关系 |
|---|---|---|---|
| **WorldLawEngine** 世界法则引擎 | 时间与物理感受 | 维护世界时钟、天气、季节、光照标签 | 造物主决定"有什么"，法则决定"此刻它处于什么环境" |
| **ChronicleEngine** 编年史引擎 | 意义与连续性 | 记录历史、命名、传说、日报 | 造物主落成建筑，编年史起名写来历 |
| **EconomyEngine** 经济与信仰引擎 | 动力循环 | 信仰/资源产出、消耗、升级门槛 | 造物主建造须申请预算 |
| **SocietyEngine** 社会关系引擎 | 社会生态 | 小人关系网、声望、派系、偶遇 | 造物主生成新小人时分配初始关系 |
| **EventDirector** 事件导演 | 节奏与惊喜 | 编排节日、危机、访客、奇观 | 造物主提供舞台，导演决定演哪出 |
| **OracleRouter** 神谕路由器 | 外部接口 | 解析外部输入为世界变更指令 | 造物主是最常调用的执行器之一 |
| **PerceptionManager** 注意力管理器 | 世界被看见的方式 | 决定哪些事件进入记忆/视野/LLM 上下文 | 为造物主生成结果做可感知性兜底 |

### 7.2 WorldLawEngine：世界法则引擎

**数据结构**：
```ts
interface WorldClock {
  day: number; hour: number; minute: number;
  timeScale: number; paused: boolean;
}
type WeatherType = 'clear' | 'rain' | 'fog' | 'snow' | 'storm';
type SeasonType = 'spring' | 'summer' | 'autumn' | 'winter';

interface EnvironmentState {
  clock: WorldClock;
  weather: WeatherType;
  season: SeasonType;
  temperature: number;
  lightLevel: number;      // 0-1，驱动全局色调
  tags: Set<string>;       // 'RAINING' | 'NIGHT' | 'FESTIVAL'
}
```

**核心系统**：`advance_clock(dt)`、`evaluate_weather()`（转移概率表 + 季节权重 + 神谕强制覆盖）、`broadcast_environment_tags()`。

**关键约束**：
- 天气必须有 3–10 秒过渡期，`lightLevel` 和粒子密度插值。
- 神谕强制天气带 TTL，过期回归自然转移。
- 任务进度用现实时间，世界时钟独立推进。

**LLM 边界**：不介入。只能通过 `WorldMagicCommand` 请求一次变更，不参与概率计算。

**验收**：10 次 `Rain(true)` + TTL 后 100% 回归自然；过渡期光照粒子连续无跳变。

### 7.3 ChronicleEngine：编年史引擎

**数据结构**：
```ts
interface ChronicleEntry {
  id: string;
  timestamp: { day: number; hour: number };
  type: 'milestone' | 'oracle' | 'social' | 'disaster' | 'festival';
  entities: EntityId[];
  facts: Record<string, any>;   // 结构化事实（事实源）
  narrative: string;            // LLM 叙事（装饰，可空）
  tags: string[];
}
```

**核心系统**：`record_fact()`（先写 facts，异步请求 narrative）、`name_entity()`（LLM 起名，失败用 `displayName + 序号`）、`generate_daily_report(day)`。

**关键约束**：
- 只追加不修改。
- facts 与 narrative 分离，日报/统计/检索基于 facts。
- 命名查重。

**LLM 边界**：起名、写叙事、生成日报摘要 → LLM；"记录什么" → 程序；LLM 不能决定"某件事没发生"。

**验收**：LLM 超时下日报仍能模板渲染；所有实体 100% 有名字。

### 7.4 EconomyEngine：经济与信仰引擎

**数据结构**：
```ts
type ResourceType = 'faith' | 'wood' | 'stone' | 'gear' | 'scroll';

interface ResourceLedger {
  balances: Record<ResourceType, number>;
  incomeHistory: { day: number; type: ResourceType; amount: number; source: string }[];
  spendHistory:  { day: number; type: ResourceType; amount: number; sink: string }[];
}
interface UpgradeGate {
  targetPrefabId: string;
  cost: Partial<Record<ResourceType, number>>;
  requires: string[];
}
```

**核心系统**：`can_afford` / `spend` / `earn`（唯一变更入口）、`evaluate_upgrade_readiness()`、`settle_task()`。

**关键约束**：
- 资源不能为负（`spend` 先校验，失败返回 `InsufficientFunds`）。
- 收入边际递减，防刷任务。
- 升级门槛数据驱动（JSON）。

**LLM 边界**：几乎不介入。可在日报解释，但不能增减资源。

**验收**：100 次随机结算账本可审计；资源永不为负；门槛 JSON 热加载。

### 7.5 SocietyEngine：社会关系引擎

**数据结构**：
```ts
interface Relationship {
  a: EntityId; b: EntityId;
  affinity: number;     // -1 ~ 1
  familiarity: number;  // 0 ~ 1
  lastMet: { day: number; hour: number };
  tags: string[];       // 'friend' | 'rival' | 'mentor'
}
interface VillagerSocial {
  reputation: number;
  faction?: string;
  relationships: Map<EntityId, Relationship>;
  recentEncounters: EntityId[];
}
```

**核心系统**：`on_proximity()`（距离小于阈值 + 都空闲 → `SocialEncounter`）、`update_affinity()`、`assign_initial_relations()`。

**关键约束**：
- 关系更新确定性（familiarity 线性累积，affinity 固定加减）。
- 偶遇有冷却（同对小人每天最多 N 次深度对话）。
- 先做对称关系，需要时再拆有向。

**LLM 边界**：偶遇对话、日终社交总结 → LLM（异步可丢弃）；数值/派系/声望 → 程序；LLM 对话不能回写数值。

**验收**：10 次偶遇 familiarity 单调递增且有冷却；LLM 离线降级预设问候，数值照常更新。

### 7.6 EventDirector：事件导演

**数据结构**：
```ts
interface EventProposal {
  eventType: 'festival' | 'crisis' | 'visitor' | 'wonder';
  title: string;
  scheduledAt: { day: number; hour: number };
  duration: number;
  participants: EntityId[] | 'all';
  worldEffects: WorldEffect[];   // 白名单枚举
  requiredResources: Partial<Record<ResourceType, number>>;
  narrative: string;
}
```

**核心系统**：`propose_event()`（LLM）、`validate_proposal()`（程序校验时间/资源/participants/effect 白名单/冷却）、`execute_event()`（拆成 `WorldEffect[]` 逐条下发）、`tick_events()`。

**关键约束**：
- **LLM 不能发明新 effect 类型**（枚举白名单）。
- 事件可中断/可回滚。
- 冷却与频率上限。

**LLM 边界**：提案内容 → LLM；可行性/资源/执行/并发 → 程序；失败降级到预设模板池。

**验收**：7 天模拟，50% 提案被拒，世界仍按模板事件运转无卡死。

### 7.7 OracleRouter：神谕路由器

**数据结构**：
```ts
type OracleChannel = 'task' | 'world_magic' | 'creator' | 'query';
interface OracleIntent {
  channel: OracleChannel;
  task?: { type: AgentTaskType; role: string; urgency: 'normal'|'high'; summary: string };
  magic?: WorldMagicCommand;
  creator?: CreatorCommand[];
  query?: { scope: 'memory'|'economy'|'society'; text: string };
  confidence: number;
}
```

**核心系统**：`ingest()`（LLM 解析 + Schema 校验）、`route()`（按 channel 分发）、`fallback()`（低置信度降级 `task/generic`）。

**关键约束**：
- **唯一入口**，任何外部输入不得绕过。
- 幂等（同 `task_id` 只处理一次）。
- 可审计（每条 intent + 路由结果写日志）。

**LLM 边界**：只解析不执行；失败走 fallback 不阻塞。

### 7.8 PerceptionManager：注意力管理器

**数据结构**：
```ts
interface PerceptibleEvent {
  event: WorldEvent;
  visibility: 'player' | 'villager' | 'llm_context' | 'internal';
  importance: number;
  ttl: number;
}
```

**核心系统**：`filter_for_player()`、`filter_for_villager()`、`build_llm_context(scope)`（按 importance × recency 取 Top-K，硬 token 预算截断）。

**关键约束**：
- 所有事件默认 `internal`，须显式声明可见性。
- LLM 上下文有硬预算。
- 玩家可见事件有节流。

**LLM 边界**：不介入。纯程序过滤器，但输出质量直接决定 LLM 上下文质量。

### 7.9 一次"神谕 + 自成长"的完整链路

```
用户: "帮我写个爬虫，顺便让世界下点雨"
  → OracleRouter.ingest → OracleIntent { channel: 'both', task: coding, magic: Rain }
     ├─► WorldLawEngine.set_forced_weather('rain', ttl=10min)
     │     → EnvironmentTagChanged('RAINING')
     │        ├─► 小人行为树: 户外者寻路进屋
     │        └─► 渲染层: 雨幕粒子 + 全局压暗
     └─► VillagerScheduler.assign(task)
           → 行礼 → 进工坊 → Workstation.Operating
              → ToolProgress → PerceptionManager 过滤 → 玩家看进度条
  → (完成) EconomyEngine.earn('faith', reward, 'oracle_task')
     ├─► ChronicleEngine.record_fact → 异步 LLM 写叙事
     └─► EconomyEngine.evaluate_upgrade_readiness()
           → UpgradeAvailable → EventDirector.propose_event()
              → 程序校验 → 造物主 upgrade + 施工动画
                 → ChronicleEngine 记录里程碑 → 日终日报
```

**LLM 只出现在四个点**：意图解析、叙事生成、事件提案、日报摘要。

### 7.10 统一纪律

```markdown
1. 所有组件只通过 EventBus 通信，禁止跨组件直接调用。
2. 所有状态变更走 WorldState 快照；每个组件提供 snapshot() / restore()。
3. LLM 输出一律视为 Proposal，必须过 Schema + 确定性校验；失败走模板降级。
4. 事实（facts/数值/关系）由程序写入；叙事（narrative/台词/命名）由 LLM 生成，可空。
5. 每个组件必须能在 LLM 离线状态下独立运转（降级模式）。
6. 资源永不为负；事件可中断可回滚；天气/时间有过渡期；命名有查重。
7. PerceptionManager 是所有事件的默认闸门：未声明可见性 = internal。
```

---

## 八、样品范围界定与流程纪律

### 8.1 样品 1.0 的目标

**让一个没看过设计文档的人，在 3 分钟内看懂"这是一个被神谕驱动的、会自己活着的像素助理世界"。**

### 8.2 范围表（保留 / 简化 / 不做）

| 保留（必须做） | 简化做 | 不做（留到样品 2.0） |
|---|---|---|
| 1 张固定预制地图 | 造物主只保留"按 JSON 放预制建筑" | 自由生成、自动修路、布局评分器 |
| 2–3 个小人 | 日常行为只做 2 种（闲逛、去工坊） | 完整 Utility AI、行为栈并发 |
| 3 个工坊：coding / research / 生活 | 每工坊 2 状态（Idle / Operating） | Overheating、长短任务分级 |
| 神谕路由接高质量 Mock + 可选真 Agent | LLM 只做意图解析 + 气泡台词 | 日报 LLM 叙事、事件提案 |
| 天气切换（晴/雨）+ 躲雨行为 | 只两种天气、无季节 | 昼夜循环、季节系统、完整光照 |
| 日终日报（DOM 叠层，模板渲染） | 编年史只记 facts，不写 narrative | 完整编年史叙事、命名系统 |
| 存档（JSON 落盘） | 单存档槽，无版本迁移 | 多存档、云同步 |
| 上帝调试面板 | 只显示实体列表 + 事件流 | 完整可视化编辑器 |
| 一个"用户下指令"入口 | 输入框 + 几个预设按钮 | 语音、快捷指令、多轮对话 |

**关键取舍**：造物主、经济、社会、事件导演在样品 1.0 中只保留数据结构和占位接口，不做完整机制。

### 8.3 样品 1.0 验收标准

1. 原生分辨率下，3 秒内能指出主角、当前神谕状态、哪个工坊在工作。
2. 输入"帮我查个资料"，小人行礼 → 进图书馆 → 翻书动画 → 进度条 → 交付日报，全程无卡死。
3. 输入"下雨"，全场小人抱头跑进屋，雨幕落下，10 秒后（TTL）自动转晴。
4. 关闭再打开，世界状态、编年史、资源账本完整恢复。
5. **LLM 断网时**，所有上述流程降级为 Mock 仍可完整走通。

### 8.4 被遗漏的产品级空白（按优先级）

**A. 产品级**

1. **用户输入入口**：游戏内像素输入框 vs 独立侧栏 vs 快捷键命令行？输入后到小人行礼之间需要有 0.5–1 秒"祭坛发光 + 暗角"的仪式感过渡。**建议样品 1.0 用底部像素九宫格 DOM 输入条 + 祭坛光柱过渡。**
2. **用户不在电脑前**：实时模拟（最简单）/ 离线快进 / 离线摘要。**建议样品 1.0 做实时模拟 + 简化离线摘要。**
3. **首屏体验**：预置初始小镇 + 30 秒引导（"点击下方输入框下达第一条神谕"），空地图开局会让样品显得没做完。
4. **静音与音量控制**：全局静音快捷键（M）、首次启动音量不过响、拟音与 BGM 分开控制。
5. **窗口生命周期**：最小化是否暂停渲染、多开存档锁、关闭时保存确认。

**B. 工程级**

6. **存档系统与 Schema 版本迁移**：存档带 `schemaVersion`，加载走迁移函数链；自动存档频率需定。
7. **上帝调试面板**：实体组件状态列表、实时事件流（最近 50 条，可暂停/过滤）、LLM 调用日志（输入/输出/耗时/降级）、手动注入事件。
8. **性能基线**：10 小人 + 20 粒子发射器 + 50 建筑，原生分辨率稳定 60fps；DOM 叠层不触发掉帧。
9. **内容生产管线**：占位素材 → 自制/AI 辅助 → Aseprite 导出；命名规范 `{category}/{name}/{state}_{frame}.png`；导入脚本。
10. **Mock 剧本库**：正常/长任务/失败/超时/取消/乱序/神谕+天气同时，与单元测试共用。

**C. 体验级**

11. **错误与空状态的视觉表达**：LLM 超时 → 工坊冒黑烟 + 小人摊手；断网 → 祭坛暗淡 + 小人望天；空任务 → 小人祭坛边发呆 + "等待神谕"气泡。**异常状态当正式美术资产做。**
12. **节奏控制**：同屏气泡上限、日报一次只展示一份、粒子全局密度上限。
13. **存档可读性**：带缩进 JSON + `debug` 字段，不要过早二进制序列化。

### 8.5 流程纪律

1. **先写 demo 剧本，再写代码**。逐秒写"3 分钟演示"：第 0 秒看到什么、第 15 秒用户输入什么、第 90 秒日报长什么样、第 180 秒关窗口重开看到什么。**剧本没写完，不进入样品开发。**
2. **每个 Phase 结束都要有可演示状态**，不能等到最后。
3. **Mock-first，真 Agent 后置**。Mock 能完整驱动 demo 剧本时再替换真接口。
4. **主分支永远可运行**，每完成一个小功能合并，随时能 `npm run dev`。
5. **美术垂直切片与代码并行**，第一周就产出切片，不要"等代码写完再画美术"。
6. **记录决策日志（ADR）**：一行标题 + 三行理由，记录"为什么把 A 改成 B"。
7. **不要过早优化**：造物主自由生成、完整光照、Auto-Tile、ECS 重构、多语言全部推迟到样品 1.0 验收后。
8. **设一个样品截止日**（建议 3–4 周硬截止），到日冻结功能进入打磨。

### 8.6 下一步动作

**花半天写 `DEMO_SCRIPT.md`**，包含：
1. 3 分钟逐秒演示脚本。
2. 样品 1.0 功能冻结清单（8.2 表格）。
3. 5 条验收标准（8.3）。
4. 截止日。

写完会发现很多"设计里没定"的东西被逼出来（用户从哪输入、离线怎么表现、失败怎么演），再带着剧本开第一个 Spike。

---

## 九、施工追踪模板

> 以下内容建议作为项目根目录 `PROJECT_PROGRESS.md`，编码 Agent 开始任务前先读本文档 + 此追踪文档。

### 9.1 编码 Agent 行动准则

```markdown
1. 状态更新：每次修改代码并通过验收后，将 [PENDING] 更新为 [DONE]，并记录更新日志。
2. 代码风格：严格 TypeScript 强类型，禁止未定义的 any。
3. 架构解耦：
   - 数据与表现分离：WorldGrid、建筑属性、小人状态存独立 State/Data 层，Phaser 场景只做渲染（View）。
   - 零阻塞原则：外部 Agent 交互、AI 推理全部异步，主循环严禁同步阻塞。
4. 视觉规范：纯 2D 像素风，开启像素点对齐，禁止抗锯齿模糊。
5. 所有 LLM 输出必须过 Schema 校验，失败走模板降级。
```

### 9.2 Phase 总览

```
[Phase 0: MVP 原型验证] ──► [Phase 1: 创世与网格基建] ──► [Phase 2: 神谕桥接通信]
     [DONE]                      [IN_PROGRESS]                  [PENDING]
                                                                  │
[Phase 6: 汇报与拟音闭环] ◄─ [Phase 5: 环境魔法与氛围] ◄─ [Phase 3: 小人双模心智]
     [PENDING]                  [PENDING]                      [PENDING]
```

### 9.3 任务卡

**Phase 0：MVP 0.1 概念验证**
- [x] TASK-0.1 极简场景搭建（单文件 main.ts，25×18 网格、祭坛、工坊色块）
- [x] TASK-0.2 双态打断测试（闲逛 → `Oracle.sendTask()` → 行礼 → 走向工坊）
- [x] TASK-0.3 工坊冒烟与交付（`completeTask()` → 小人走出复位）

**Phase 1：创世与网格基建**
- [ ] TASK-1.1 工程目录重构与数据类型定义 [IN_PROGRESS]
- [ ] TASK-1.2 网格账本与空间分配器（WorldGrid & findBestSlot）
- [ ] TASK-1.3 建筑预制件蓝图库（PrefabRegistry + prefabs.json）
- [ ] TASK-1.4 造物主生成系统与自动铺路

**Phase 2：神谕协议与异步桥接**
- [ ] TASK-2.1 神谕消息协议定义（Oracle Protocol + 状态机枚举）
- [ ] TASK-2.2 双向通信管理器（OracleBridge，WebSocket + Mock 注入）

**Phase 3：小人双模心智**
- [ ] TASK-3.1 网格 A* 寻路 + Y-Sort
- [ ] TASK-3.2 日常行为状态机
- [ ] TASK-3.3 神谕高优打断机制（行为栈）

**Phase 4：工坊具身化与工具绑定**
- [ ] TASK-4.1 工坊多状态动画控制器（Idle/Operating/Overheating/Cooling）
- [ ] TASK-4.2 长短任务分级表现

**Phase 5：环境法则与世界魔法**
- [ ] TASK-5.1 天气与粒子系统
- [ ] TASK-5.2 言出法随交互（下雨 → 户外小人避雨）

**Phase 6：世界内汇报、拟音与经济**
- [ ] TASK-6.1 《神谕日报》像素组件
- [ ] TASK-6.2 程序化"哇啦哇啦"拟音器
- [ ] TASK-6.3 信仰结晶与自进化扩建结算

### 9.4 技术风险优先级（Spike First）

```markdown
- [ ] SPIKE-1 造物主布局器：Zone + PrefabBlueprint + findBestSlot + 布局评分器
      验收：20条CreatorCommand，100%无重叠、主路连通、分区纯度>90%
- [ ] SPIKE-2 神谕状态机：Mock异常注入（乱序/重复/超时/取消/断线）
      验收：所有异常下小人/工坊状态可恢复，无卡死
- [ ] SPIKE-3 行为栈：日常/神谕抢占/恢复/并发排队
      验收：3神谕+1取消+下雨避雨，无抖动无瞬移
- [ ] ART-0 垂直切片：1场景+1页视觉规范+A/B/C细节预算
      验收：原生分辨率3秒内指出主角/路径/交互点；400%放大无接缝错位

以上4项完成前，不进入 Phase 1 正式开发。
LLM 只输出 CreatorCommand 高层JSON，坐标由确定性分配器计算。
光照先伪光照（全局调色+发光贴图+混合模式），Light2D 列为 P3。
```

### 9.5 交接提示词模板

```markdown
你好！请作为"AI原生像素神谕个人助理世界"的专业前端与游戏架构工程师。
项目基于 Phaser 3 + Vite + TypeScript，正在执行主干线施工计划。

【当前施工进度】
- 已完成：Phase 0（极简单文件 MVP 0.1 原型已验证）。
- 当前目标任务：#TASK-1.1 与 #TASK-1.2（工程模块化重构、网格账本、造物主空间选址器）。

【本轮具体施工要求】
1. 重构工程目录，拆分 types、grid、creator、scenes 模块。
2. 实现 src/types/grid.ts 与 src/types/blueprint.ts 强类型定义。
3. 实现 src/core/grid/WorldGrid.ts：
   - 管理 25x18 网格的 ZoneType、TerrainType、Occupant、Walkable。
   - 提供 findBestSlot(footprint, zone, margin) 算法。
4. 保证 TypeScript 严格编译无报错，并提供最小单元测试/控制台验证示例。

请根据以上工单直接开始施工，输出规范代码文件与改动说明。
```

---

## 附录 A：MVP 0.1 参考代码（已归档）

> 单文件 `src/main.ts` + `index.html` 的完整实现已在 Phase 0 验证通过，包含：网格背景、祭坛/工坊色块、小人闲逛、`window.Oracle.sendTask()` / `completeTask()` 接口、工坊粒子冒烟。若需复现，参考对话历史中的完整代码片段。核心结构：

```
SandboxScene
  ├─ drawGridBackground()
  ├─ setupFactorySmoke()      // 6×6 像素纹理粒子
  ├─ receiveOracleTask()      // 行礼 → 移动 → 工坊
  ├─ completeOracleTask()     // 出门 → 汇报 → 复位
  └─ update()                 // 闲逛计时器
```

## 附录 B：关键决策索引（ADR 简表）

| # | 决策 | 理由 | 日期 |
|---|---|---|---|
| ADR-1 | 引擎选 Phaser 3 而非 Bevy | Web UI 丰富度 + Agent 通信零摩擦 + 迭代速度 | 讨论中期 |
| ADR-2 | 不引入重型 ECS，手写数据层 | 避免与 Phaser GameObject 生命周期冲突 | 讨论中期 |
| ADR-3 | 光照先伪光照，Light2D 留 P3 | 像素完美优先于动态光照 | 美术讨论 |
| ADR-4 | LLM 只输出高层 JSON，坐标确定性计算 | 造物主可控性根基 | 技术难点讨论 |
| ADR-5 | 99% 行为本地规则，LLM 仅低频介入 | Token 成本 + 帧率稳定性 | 分工讨论 |
| ADR-6 | 样品 1.0 砍掉造物主自由生成 | 防止范围无限膨胀 | 样品界定 |
| ADR-7 | 先写 DEMO_SCRIPT 再写代码 | 防止不断加功能 | 流程纪律 |

---

**文档结束。** 本文档为设计基线，后续变更须同步更新对应章节并在附录 B 追加 ADR 记录。