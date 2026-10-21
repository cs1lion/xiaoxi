# Chief 启动入口：AI 原生像素个人助理世界

> 用途：给 Chief of Staff 的唯一启动入口。本文是执行约定，不代替源设计文档。

## 0. 开始前提

Chief 只有在**能够访问真实项目工作目录或代码仓库**时，才能核验代码、运行项目并正式分派施工任务。本启动包仅包含项目文档与任务模板，**不包含真实应用源码，也不代表源代码已存在于此目录**。

启动时由用户提供或在已授权开发环境中确认 `PROJECT_ROOT`。不得根据文档名、旧对话或猜测自行认定路径。若无法访问仓库，先报告阻塞，不得假装已检查代码。

## 1. 资料阅读顺序

1. 本文件 `README_CHIEF_START.md`。
2. `docs/PROJECT_BRIEF.md`：当前简明执行基线。
3. `docs/PROJECT_PROGRESS.md`：文档记录的进度与待核验项。
4. `docs/DEMO_SCRIPT_TEMPLATE.md`：样品 1.0 演示流程；需结合实际项目补完并批准。
5. `docs/ART_SPEC_TEMPLATE.md`：视觉规范模板。
6. `docs/source/01-design-and-construction-decisions.md`：较新的设计与施工决策汇总。
7. `docs/source/02-architecture-discussion-archive.md`：早期架构讨论存档。该文件包含多个探索路线，不能把其中每个备选方案都当成最终决定。

## 2. 当前决策优先级

当资料之间存在冲突时，按以下顺序处理：

1. 用户在当前对话中明确确认的最新决定。
2. `docs/PROJECT_BRIEF.md` 中标为“已确认”的决定。
3. `docs/source/01-design-and-construction-decisions.md` 的最终决策章节。
4. `docs/source/02-architecture-discussion-archive.md` 中的历史讨论与备选方案。
5. 任何未经验证的旧进度文字。

不擅自把冲突静默改成一种答案。记录冲突、证据和建议，请用户或 Architect 决定。

## 3. 第一个工作任务：只读基线核验

先做只读调查，不修改业务代码、不重构、不创建另一套重复工程。

- 确认 `PROJECT_ROOT`、分支、工作区是否干净、最近提交。
- 检查 `package.json`、锁文件、`vite.config.*`、`tsconfig.*`、`index.html`、`src/`、测试目录、README 和现有进度文件。
- 核验 Phaser 3 + TypeScript + Vite 是否已经是实际技术栈。
- 核验文档声称的 Phase 0 MVP 是否真实存在、能否启动、测试是否可重复。
- 核验 `TASK-1.1`、`TASK-1.2` 的真实完成度，不根据旧文档直接标记为 DONE 或 IN_PROGRESS。
- 对照 `docs/PROJECT_BRIEF.md`，列出实现与文档的差距、风险、缺失文件和最小下一步。
- 在修改任何代码前，先提交调查报告给用户；如发现重大架构问题，再请 Architect 审查。

### 第一次交付

生成或更新以下文件（先查看仓库中是否已有同名文件，增量更新，不覆盖有价值内容）：

- `PROJECT_PROGRESS.md`：实际状态、证据、任务与下一步。
- `docs/REPO_RECON.md`：真实仓库结构、启动方法、测试方法、代码调用入口和未知项。
- `docs/DECISIONS.md` 或 ADR 记录：记录核验后的决策与冲突，不把推断写成事实。

首轮结束时只汇报事实与计划，不要自动开启大规模编码。

## 4. 角色协作

- **Chief**：唯一总协调者；维护任务列表、依赖、状态和汇报，不亲自接管全部专业实现。
- **Architect（强模型）**：重大架构、跨模块、数据状态、安全、异步一致性和难以定位的问题；按需召集，不常驻参与所有任务。
- **Pixel Art Director（中等模型）**：视觉规范、截图评审、可执行的美术任务。
- **World Builder（普通模型）**：经批准后的样品实现与局部迭代。
- **Agent Integration Engineer（中等模型）**：未来与已有 Agent 助理的接口适配；样品阶段先只读调查。
- **QA Engineer（中等模型）**：独立运行、测试和验收；没有证据则写“未验证”。
- **Memory & Voice Input Engineer（中等模型）**：记忆、自进化、用户语音指令输入；目前不参与像素样品施工，除非明确分配专项任务。
- **Research & Documentation Assistant（普通模型）**：资料核验、决策记录和交接文档。

每个代码任务只有一个主实施者。群聊建议不等于代码已修改；施工完成不等于验收通过。

## 5. 第一阶段建议顺序

1. 核验工作目录与现状（只读）。
2. 补完并批准 3 分钟 `DEMO_SCRIPT.md`，明确样品 1.0 的演示内容和边界。
3. Pixel Art Director 制作一页 `ART_SPEC.md` 与垂直切片验收标准。
4. 若 Phaser/Vite/TS 与文档一致，按真实进度从最小任务继续，不重新选型。
5. 分阶段实现、每个阶段可运行、QA 复验、Chief 更新进度。
6. 样品与接口边界清楚后，再进行 Agent 适配层的正式集成。

## 6. 绝对约束

- 不凭旧文档声称代码、测试或 Phase 状态已经验证。
- 不随意重写个人 Agent 助理、记忆、自进化或任务调度核心。
- 不让 LLM 直接改世界事实、坐标、资源账本或内部状态；提案必须走 Schema 与确定性校验。
- 外部 Agent/LLM 通信不得阻塞渲染主循环。
- 不把 Bevy 的历史探索代码直接混进 Phaser 实现。
- 用户最新要求：**不需要语音陪伴、TTS 或主动语音播报；语音仅作为用户输入指令的方式**。根据源文档的样品 1.0 范围，语音输入默认不进入当前 MVP，除非用户另行批准扩充范围。
