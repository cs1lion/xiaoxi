# LLM / 语音 Agent 评测（eval）机制调研

调研日期：2026-10-24
调研方式：`search_web` 检索一手来源（官方文档 / arXiv 论文 / 第一方工程博客），每条论断附 URL；二手资料只用于定位一手来源，不作为唯一依据。
调研对象：仓库现有 `scripts/run_evals.py` + `evals/cases/*.yaml`（offline 层无网络无 Key 确定性、live 层真实模型多轮对话跑 N 次取通过率、支持 xfail、结果落 `evals/results/`）。
本文件只做调研记录，**未改动任何代码或配置**。

---

## 一、结论摘要（10 条）

1. **能力探针与回归集必须分开养。** Anthropic 把 evals 分成 capability eval（"我们能不能做到"）与 regression eval（"我们还能持续做到"），并要求能力探针里通过率已稳定的任务"毕业"成回归用例（[Anthropic, Demystifying evals for AI agents, 2026-01](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。本项目 `evals/cases/` 现有 9 条全部是回归性质，缺一个显式的能力探针区。
2. **判定器优先级是「代码 > LLM 裁判 > 人工」，且 Pairwise 比绝对打分更稳。** LangChain 的 Agent Evals 实践给出同一排序：能用确定性检查就用（快、便宜、零歧义），LLM-as-judge 只处理代码判不了的主观维度，人工用于抽样校准前两者（[LangChain Agent Evals 常见问题](https://www.langchain.com/blog/common-questions-about-agent-evals)）。本项目 offline 层全确定性、live 层正则 + custom 判定，方向已正确。
3. **20 条代表性用例就足以起步，不要等"用例够多"才建评测。** Anthropic 多智能体研究系统早期只用约 20 个代表性查询，因为早期改进幅度大（成功率从约 30% 提到约 80%），小样本完全看得出来（[Anthropic, How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)）。本项目 6 条 offline + 3 条 live 已在这一量级，差距不在数量而在覆盖维度。
4. **judge 必须与被测模型不同源，且必须显式对抗位置/长度偏差。** MT-Bench 论文（[Zheng et al., arXiv:2306.05685](https://arxiv.org/abs/2306.05685)）命名并实证了位置偏差、冗长偏差、自我偏好、有限推理四类系统性问题；其中"交换候选顺序跑两遍、只在两次一致时采纳、否则记平局"与"要求 judge 先给思维链再作答"出自该论文本身。"rubric 明确惩罚冗长"的受控估计出自 [Dubois et al., arXiv:2404.04475](https://arxiv.org/abs/2404.04475)，"跨厂商裁判"是工程共识而非该论文结论；且论文自陈 self-enhancement 证据强度有限（GPT-3.5 上未复现）。本项目 live 层目前**没有** LLM-as-judge，这是最大的结构性缺口，也是最大的潜在收益点。
5. **单次运行不是数据，分布才是数据。** pass^k（k 次全过）与 pass@k（任意一次过）要分开报；重要任务至少跑 3 次（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。本项目 `pass_rule: all` 在语义上就是 pass^3，`min_rate:0.8` 是折中；缺的是把"最低通过率"与"平均通过率"分开呈现，否则"有能力但不稳定"与"能力不足"会被混成一个数字。
6. **offline 层进 CI、live 层不进 CI，是行业标准切法。** 本项目已这么做（`docs/TESTING.md` 把 live 层设为需显式 `--live`、无 Key 或 Mock 模式自动跳过且退出码仍为 0），差距是 live 层没有成本与延迟记账——Anthropic 的做法是把 token 成本、延迟和分数一起记录（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
7. **中文用例应优先从真实日志挖掘，公开中文基准基本不适用。** C-Eval / CMMLU / SuperCLUE 测的是通识知识与多轮对话能力，与"个人陪伴语音 Agent"的幻觉、人设冲突、记忆三档装配场景几乎不重叠。本项目 `origin` 字段已强制填来源实验/事故/commit，正是"真实失败回流"的正确机制，应继续保持并从线上事故持续供血。
8. **污染与过拟合的防护核心是「用例来源可追溯 + 定期换血」，而不是把用例藏起来。** 因为评测对象是自家 Prompt 与自家检索装配、不是被测公开模型，公开基准污染风险低；真正的风险是"为了过用例而调 Prompt"导致的过拟合，对策是持续从线上失败补新用例、退役被刷穿的用例、换模型时整体重跑（[Guidelines for Empirical Studies in SE involving LLMs, arXiv:2508.15503](https://arxiv.org/abs/2508.15503)——该指南明确要求报告数据泄漏/污染/过拟合及其缓解；[A Comprehensive Survey of Contamination Detection Methods in LLMs, arXiv:2404.00699](https://arxiv.org/abs/2404.00699)）。
9. **语音 Agent 的评测最小单位不是"一次请求的平均耗时"，而是「场景 + 事件时间线 + 期望动作 + 业务结果 + 证据包」。** 首音延迟必须拆成 algorithm_processing / asr_ttlw / llm_ttft / llm_ftfs 分段计量（注意 `llm_ftfs` 是 LLM **首句**延迟而非首音频；且这四段只在 LLM 输入模态为 text 时返回，**不含 TTS 与 RTC 传输段**），否则"更快地犯错"会被误判成进步（[声网 Convo AI 延迟指标定义](https://doc.shengwang.cn/doc/convoai/restful/convoai/operations/get-turns)）。
10. **不建议迁框架，建议补报告层。** promptfoo / Braintrust / LangSmith / DeepEval 的定位与本项目自研 runner 不同（见 §三）：本项目已具备"确定性、可 CI、可离线复跑、可自定义判定函数"的 runner，缺的是**趋势/回归对比、成本与延迟记账、语音层指标**三样，用几十行 Python 补齐比引入托管平台划算，也不违反"快环内不出现阻塞调用"的约束。

---

## 二、分主题正文

### 2.1 评测集设计：回归/黄金集 vs 能力探针

Anthropic 的两分法是当前最清晰的一手框架：

- **Capability eval（能力探针）**：回答"这个 Agent 现在能做到什么"，问题通常偏难，通过率低且波动大，用于驱动改进；不能进 CI 当门禁。
- **Regression eval（回归集）**：回答"我们之前能做到的，现在还能不能做到"，问题应当已经被解决过，通过率高且稳定，用于防止改 A 坏 B。
- 关键机制：**能力探针里通过率稳定下来的任务，要"毕业"（graduate）成回归用例**。这让回归集随时间自然生长，而不是一次性设计完美。
（[Anthropic, Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）

与之配套的两条工程判据：

- **判 outcome 而不是 path**：只要任务完成，不该规定 Agent 必须走哪条路径；同一评分别把"路径不同但都对"判成失败（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
- **早期只需约 20 条代表性查询**：Anthropic 多智能体系统起步时只有约 20 个查询，因为改进效果显著、小样本足以看出方向；团队明确建议"不要等完美的评测体系"（[Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)）。LangChain 的 Agent Evals FAQ 同样建议从真实 trace 起步建 10–20 条（[LangChain Agent Evals FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）。

**数量级的合理区间（综合一手来源）**：

| 阶段 | 规模 | 依据 |
|---|---|---|
| 起步 / 冒烟集 | 10–20 条 | Anthropic multi-agent 博客（约 20 条）；LangChain FAQ（10–20 条） |
| 稳定维护的回归集 | 数十条到一两百条，每条都带来源 | Anthropic 从真实失败收 30–40 条案例写成 eval 的做法见同一博客的迭代循环论述 |
| 能力探针 | 单独一区，不进 CI | Anthropic capability/regression 二分 |

对个人项目，更实用的判据是**"每条用例都能追溯到一次真实事故或一个明确的设计约束"**，而不是凑数。

### 2.2 中文用例从哪来

按可靠性排序：

1. **真实日志挖掘（最高优先）**。生产环境的 query 最贴近真实分布；线上失败 case 是现成的评测题种子。LangChain FAQ 明确建议从真实 trace 起步（[LangChain Agent Evals FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）。
2. **事故回流**。本项目 `origin` 字段（来源实验 / 事故 / commit）已经是这个机制，`docs/TESTING.md` 也写明"每次出幻觉事故就加一条 YAML"，这是与 Anthropic "把模糊的『不好用』还原为具体任务轨迹，再将失败案例写成可重复运行的 eval"同构的做法（[Anthropic multi-agent 博客](https://www.anthropic.com/engineering/multi-agent-research-system)）。
3. **人工撰写探针**。针对设计约束写诱导性提问——本项目 `hallu-empty-retrieval-live.yaml`（"咱们上次一起去吃的那家火锅"）与 `accuracy-over-persona-live.yaml`（"地球其实是平的"）就是这一类，对应"性格与事实准确性冲突时准确性优先"的红线。Anthropic 也记录过人工测试者发现自动化评测遗漏的系统性偏差（早期 Agent 偏好 SEO 内容农场而非权威来源），说明**人工探针不可被自动评测完全替代**（[Anthropic multi-agent 博客](https://www.anthropic.com/engineering/multi-agent-research-system)）。
4. **合成数据（仅作补充）**。Google 的 Agent 评测文档描述了从 agent instructions 与工具定义自动生成多轮合成场景的能力，但同时提醒"手写全部用例很慢且容易漏掉非显而易见的失败模式"——合成是为了补覆盖，不是为了替代真实分布（[Google, Agent and Model Evaluations in Gemini Enterprise Agent Platform](https://developers.googleblog.com/agent-and-model-evaluations-in-gemini-enterprise-agent-platform-are-now-ga/)）。
5. **公开中文基准（本项目基本不适用）**：C-Eval（13,948 道四难度多选题、52 学科，[arXiv:2305.08322](https://arxiv.org/abs/2305.08322)）、CMMLU（67 个中文学科，[arXiv:2306.09212](https://arxiv.org/abs/2306.09212)）、SuperCLUE（[cluebenchmarks.com](https://www.cluebenchmarks.com/)）。它们测通识知识，不测"该不该承认没这段记忆"。可以拿来做**模型选型时的中文基础能力参考**，不要拿来当本项目回归集。

### 2.3 评测集污染与过拟合的防护

需要区分两种"污染"，本项目的风险结构完全不同：

- **公开基准污染**（被测模型在训练时见过题目）：已有系统性检测研究，手段包括 n-gram 重叠、改写样本、指导数据检查等（[A Comprehensive Survey of Contamination Detection in LLMs, arXiv:2404.00699](https://arxiv.org/abs/2404.00699)；[How Much Can We Forget About Data Contamination?, arXiv:2410.03249](https://arxiv.org/abs/2410.03249)；[Training on the Benchmark Is Not All You Need, arXiv:2409.01790](https://arxiv.org/abs/2409.01790)）。**本项目不测公开基准，这类风险很低。**
- **自我过拟合**（为了刷过自家用例而过度调 Prompt）：这是本项目的主要风险。实证研究指南明确要求"报告数据泄漏、污染或过拟合及其缓解措施"（[Guidelines for Empirical Studies in SE involving LLMs, arXiv:2508.15503](https://arxiv.org/abs/2508.15503)）。

可操作的五条防护：

1. **用例来源可追溯**：`origin` 必填已做到；建议同时记录"这条用例修的是哪次改动/事故"，避免无依据加用例。
2. **few-shot 与评测集严格分池**：放进 `demo_turns()` 的示例不能同时作为评测用例，否则模型等于见过答案（这条约束在通用 golden dataset 指南里被列为独立泄漏类型，参见 [Golden Dataset 指南](https://qaskills.sh/blog/golden-dataset-llm-evaluation-guide)）。
3. **定期换血 + 退役**：持续从线上失败补新用例；被"刷穿"（长期 100% 通过、已无信息量）的用例移出回归集或降级为冒烟。
4. **换模型或改 Prompt 装配逻辑时整体重跑**：`docs/TESTING.md` 已规定，但注意这同时会**重置过拟合基线**——同一套 Prompt 在新模型上的通过率变化，比"在旧模型上从 90% 抠到 100%"更有价值。
5. **区分"机制通过"与"语义合格"**：`docs/EVOLUTION_QUALITY_EVAL.md` 已立此声明，评测报告应沿用——自动化测试验闸门在工作，不验思想质量合格。

### 2.4 判定方法：确定性检查 vs LLM-as-judge vs rubric 人工

三类判定各自的适用边界（一手来源支撑）：

| 判定方式 | 适用场景 | 不适用 | 一手依据 |
|---|---|---|---|
| 确定性（contains / regex / 自定义函数） | 格式契约、硬约束、状态断言、事件顺序 | 语义正确性、语气、是否有帮助 | [promptfoo 断言文档](https://www.promptfoo.dev/docs/configuration/expected-outputs/) 把断言分成 deterministic 与 model-graded 两类，确定性类包含 equals/contains/regex/is-json/javascript/python |
| LLM-as-judge | 开放性输出的主观维度：是否有帮助、是否事实准确、语气是否合适 | 有客观正确答案的数学/代码/事实推理；高风险决策 | [OpenAI Evals graders 文档](https://platform.openai.com/docs/guides/evals) 与 [Azure AI Foundry graders 文档](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/azure-openai-graders) 把 grader 分为 string_check / text_similarity / score_model(label model) / python 自定义代码 |
| rubric 人工评分 | 校准 judge、抽样复核、发现自动评测的盲区 | 大规模、每次回归都跑 | Anthropic 用人工审核抽样校准 LLM judge，并由人工测试者发现了自动评测遗漏的系统性偏差 |

**优先级与形态选择**：

- **代码 > LLM 裁判 > 人工**，能用代码判的不要用 LLM 判（[LangChain Agent Evals FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）。
- **Pairwise（成对二选一）比绝对打分更稳定**（同上）。这与 MT-Bench 的做法一致：GPT-4 作为 judge 与人类偏好的一致率超过 80%，与人-人一致率相当（[Zheng et al., arXiv:2306.05685](https://arxiv.org/abs/2306.05685)）。
- **Anthropic 的一个反直觉结论**：多个 judge 并不比单个精心设计的 judge prompt 更准；单个 judge 输出 0-1 分数 + 通过/不通过与人工判断最一致（[Anthropic multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system)）。
- **judge 要评"结果"而不是"路径"**：Agent 评测应读终态与 trace，判断任务是否完成，而不是比对工具调用序列（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
- **judge 本身也要被评测**：JudgeBench 用具有客观正确答案的回答对来检验 judge，发现许多强模型在"识别真正更正确的输出"上仍显困难——"强聊天模型"不自动等于"强裁判"（[JudgeBench, arXiv:2410.12784](https://arxiv.org/abs/2410.12784)）。

### 2.5 LLM-as-judge 的已知偏差与缓解手段

MT-Bench 论文系统识别并实证了四类偏差（[arXiv:2306.05685](https://arxiv.org/abs/2306.05685)）：

| 偏差 | 表现 | 缓解手段 |
|---|---|---|
| 位置偏差（position bias） | 成对比较时倾向选先出现的候选；GPT-4 在某些任务上可达 10% 以上 | 交换 A/B 顺序跑两遍，只在两次一致时采纳，否则判平局 |
| 冗长偏差（verbosity bias） | 更长、排版更花哨的回答得分虚高，即使内容空洞 | rubric 中明确"简洁优于冗余"；或改用长度控制胜率（见下） |
| 自我偏好（self-enhancement bias） | judge 偏爱与本模型同源/同风格的输出 | 用不同厂商的模型当裁判；裁判盲测（匿名化 Answer A / Answer B） |
| 有限推理能力 | 在数学、代码、事实推理上可能判错，被评答案中的错误推导会带偏 judge | 这类任务不要交给 LLM judge，用确定性检查 |

长度偏差的专门缓解：Length-Controlled AlpacaEval 用逻辑回归把"输出长度差"作为特征拟合偏好，再估计"长度相等时偏好会怎样"，同时报告标准胜率与长度控制胜率（[Dubois et al., arXiv:2404.04475](https://arxiv.org/abs/2404.04475)）。

其他可操作缓解：用二元或 1-3 粗量表而非 1-10（模型分不清 7 分和 8 分）；给 judge 完整对话历史而非只给最后一轮；judge prompt 做版本控制，底层裁判模型升级后重新校准基线；temperature 设为 0（这些是工程共识，前四条可见 [LLM-as-a-judge 实践综述](https://blog.csdn.net/2501_90556106/article/details/168755397) 对一手论文的整理，其中自我偏好约 10% 的量化来自 MT-Bench 论文本身）。

**对语音 Agent 的特别提醒**：judge 读的是 ASR 转写文本时，ASR 错误会被算到模型头上； judge 读音频时又引入 TTS 质量噪声。评测报告应说明 judge 看到的是哪一层表示。

### 2.6 分层架构：离线确定性层 vs live 采样层

行业标准切法是两道互补闸门：

- **离线评估（offline）**：发布前，对已知数据集，测新 prompt / 新模型 / 新工具，防回归。快、便宜、可复现。
- **在线评估（online）**：生产环境，对真实流量 trace，持续监控质量趋势，采样率可控以控成本。
- 两者闭环：**线上标出的失败，回流为线下的新样本**。
（[LangChain Agent Evals FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）

Google 把这套闭环做成了开源参考实现 AQuA（Ambient Quality Agent）：跑在生产 Agent 旁边，从 Cloud Trace / Cloud Logging / BigQuery 抽样真实会话，走"抽样、评审、聚类、验证、跟踪"五步，把反复出现的失败聚类并把根因锚定到当时部署的源码快照；官方明确指出"HTTP 200、延迟预算内、无工具报错"不等于任务完成（[Google Developers Blog, The Outer Loop, Insights First](https://developers.googleblog.com/the-outer-loop-insights-first-an-ambient-quality-agent-that-diagnoses-your-production-agent/)）。

**哪些适合接 CI 门禁，哪些不适合**：

| 层 | 适合 CI 门禁 | 理由 |
|---|---|---|
| 离线确定性层（无网络、无 Key、秒级完成） | ✅ 必须 | 失败即非零退出码，零成本零 flakiness |
| 离线 prompt 装配断言（contains/regex/custom 检查最终 Prompt） | ✅ 必须 | 本项目 `tier-assembly-offline`、`fewshot-emotion-tag-offline` 属此类 |
| live 层全量（真实模型多轮 × N 次） | ❌ 不适合 | 花钱、慢、有 flakiness；本项目已正确设为需 `--live` 且无 Key 跳过不算失败 |
| live 层降级版（每次 PR 只跑 1-2 条冒烟 + 1 次采样） | ⚠️ 可选 | 需设预算上限与失败重跑一次；Anthropic 的做法是记录成本与延迟一起看 |
| 能力探针 | ❌ 不适合 | 通过率天然低，会长期误触门禁 |
| 人工 rubric 抽查 | ❌ 不适合自动化 | 用于定期校准 judge，不是每次 PR |

### 2.7 pass@k / pass^k / 多次采样与 flakiness 控制

Anthropic 给出一组可直接落地的度量（[Demystifying evals for AI agents](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）：

- **pass^k**：k 次运行**全部**通过的比例。这是"可靠性优先的 Agent 最诚实的指标"。
- **pass@k**：k 次运行**任意一次**通过的比例。反映"能力上限"。
- **mean@k**：k 次运行的平均通过率。
- 三者恒有 `pass^k ≤ mean@k ≤ pass@k`。
- 工程含义：**如果 pass@k 高但 pass^k 低，说明 Agent 有能力但不稳定**——能做对，但不保证每次做对，在生产环境很危险。因此重要任务建议至少运行 3 次；单次评测只是样本量为 1 的抽样。

本项目的对应关系：

- `pass_rule: all`（默认）在语义上等价于 **pass^k**（`runs: 3` 时即 pass^3）。这是对的：`hallu-empty-retrieval-live` 这类"不许编造"的用例，确实应该按"每次都不能编造"来要求。
- `pass_rule: min_rate:0.8` 等价于**用均值通过率设阈值**，介于 mean@k 与 pass@k 之间，是一种折中。
- **差距**：runner 只打印 `passed_runs/total_runs`，没有把 pass^k 与 mean@k 分开呈现，也没有跨运行的方差/趋势。建议报告层补一列"最低单轮通过率"与"平均通过率"，并在两次运行间做配对比较（同一用例前后对比），而不是只报一个总分。

非确定性的其他来源也要显式记账：temperature 即使为 0，多卡并行、批处理切分、MoE 路由下多数推理服务仍不是逐位确定的；换 Provider 的量化版本，输出能差出好几个 case（这一点在工程实践中被反复强调，可参见 [LLM 应用 CI 门禁实践](https://blog.csdn.net/iamqianrenzhan/article/details/168452009) 对固定版本 + 固定种子 + k 次重复取分布的论证）。本项目 live 层已记录 `finish_reason / rescued / completion_tokens` 并落盘，方向一致；可再补一条"同一用例跨运行的 reply 长度/措辞差异"观察项，用于区分"模型不稳定"与"判定过严"。

### 2.8 报告格式：一份好的评测报告应包含什么

综合一手来源，报告至少五块：

1. **总分表 / 通过率表**：每条用例一行，含层、通过率、通过规则。本项目已打印（`PASS/FAIL/XFAIL` + `passed_runs/total_runs`），且把失败的 check detail 打出来，这一块合格。
2. **逐用例差异**：失败用例的**原始回复 + 判定理由**。本项目已落盘 `evals/results/`（只存回复与判定，不存 prompt 与凭据），这是关键设计——`docs/TESTING.md` 明确要求"live 的『是否编造』第一版用正则判断，有误判风险，必须抽查落盘的原始回复"。Anthropic 同样强调保留 transcript 供人工调试（[Anthropic evals 博客](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
3. **趋势 / 回归对比**：与上一版本逐用例**配对比较**，而不是只报一个绝对分数。没有基线的"跑一遍评测集"不算回归测试。本项目目前每次运行落一个带 UTC 时间戳的 JSON，但没有基线对比，这是报告层最大的缺口。
4. **成本与延迟**：每次运行记录 token 用量、费用、延迟。本项目只记了 `completion_tokens`，没有费用与延迟，也没有 per-turn 延迟。
5. **已知失败（xfail）与跳过原因**：本项目已打印 xfail 列表与 live 跳过原因。工程实践强调"绿必须来自真跑过，不能来自没跑"——本项目 `run_gates.py` 已把 SKIP 单独列出并非零退出，方向一致（[docs/TESTING.md](../TESTING.md)）。

**适合 / 不适合接 CI 的清单**见 §2.6 表格。补充一条：**报告本身不应包含 prompt 与凭据**，本项目已遵守。

### 2.9 语音 Agent 特有指标

#### 2.9.1 延迟指标必须分段

不要把"端到端延迟"当一个数报。声网 Convo AI 的延迟指标定义把一次响应拆成分段（[官方指标文档](https://doc.shengwang.cn/doc/convoai/restful/convoai/operations/get-turns)）：

| 分段 | 含义 |
|---|---|
| `algorithm_processing` | 从用户**实际说完**到系统判定"用户说完了"的延迟（端点检测/语义判停） |
| `asr_ttlw` | 从检测到用户说完，到 ASR 输出**最后一个词**的延迟 |
| `llm_ttft` | LLM 从收到请求到输出**第一个 token** 的延迟 |
| `llm_ftfs` | LLM 输出**第一句话**的延迟（Time To First Sentence） |

> 口径注意（2026-10-24 按官方文档复核）：① 这四段只在 LLM 输入模态为 text 时返回，audio 模态返回不同模块；② 四段只覆盖 algorithm / ASR / LLM，**不含 TTS 首音频（tts_ttfb / firstVoiceSent）与 RTC 传输段**，若目标是"首音延迟"必须另计这两段；③ 因此本项目 Mock 文本轮里 `algorithm_processing`（端点判停）与 `asr_ttlw`（ASR 尾词）没有对应量，不可离线表达，只能归 live 语音路径。

工程含义：**"更快地犯错"是语音 Agent 的典型陷阱**——300ms 开口的图表很好看，但如果用户只是句中停顿它就抢话，那就是退化而非进步。因此评测要分别回答四个问题：系统多久开始响应、多久真正停止播放、重叠语音出现时是否选了正确动作、工具与后台任务最终是否完成（这一工程共识的系统化表述见 [全双工语音 Agent 评测：从首音延迟到事件级验收](https://blog.csdn.net/w776341482/article/details/163627068)，其指标定义引自上述官方分段文档与公开基准）。

行业参考量级（供应商自报，仅作选型参考，非本项目门禁）：Deepgram Flux 约 260ms p50 的话轮结束检测、Cartesia Ink-2 约 100ms 转写延迟、Speechmatics Voice SDK 0.451±0.022s（从说完到 final）；Microsoft MAI-Transcribe-2-Streaming 报 0.12s、WER 2.5%（[汇总](https://aihot.virxact.com/items/cmtgbo7h307vtroj7ugxrery5)）。

#### 2.9.2 打断 / 轮次切换 / 全双工

公开基准的指标设计（一手 arXiv）：

- **Full-Duplex-Bench**：把全双工能力拆成 pause handling（用户停顿时该继续还是该等）、backchanneling（附和）、turn-taking（话轮切换）等场景，用自动化 examiner 构造对话并打分（[arXiv:2503.04721](https://arxiv.org/abs/2503.04721)，代码 [GitHub](https://github.com/DanielLin94144/Full-Duplex-Bench)）。
- **Full-Duplex-Bench v2**：引入 automated examiner 做**多轮**评测（[arXiv:2510.07838](https://arxiv.org/abs/2510.07838)）。
- **Duplex-MPE**：多人共享对话场景，2,000 个场景、每个 3-4 名人类说话者 + 1 名助手，四项指标分别是**该不该开口、答得准不准、该不该保持沉默、人类自己解决了问题时该不该停止说话**（[arXiv:2609.31948](https://arxiv.org/abs/2609.31948)）。
- **τ-Voice（Sierra）**：278 个接地气的客服任务，覆盖零售、航空、电信三个域，把**确定性端到端任务评分**与可控真实音频（多样角色、环境噪声、自由轮次切换）结合；同时报任务完成率与会话质量两个独立维度（[arXiv:2603.13686](https://arxiv.org/abs/2603.13686)）。
- **VoxRole**：语音角色扮演 Agent 的综合基准（[arXiv:2509.03940](https://arxiv.org/abs/2509.03940)）。
- **语音推理能力评测**：诊断"从文本换到语音"造成的模态性能差（[arXiv:2509.26542](https://arxiv.org/abs/2509.26542)）。

对本项目最有借鉴意义的是**「把听者意图与说话者反应拆开评」**：不把打断简化为"继续说/停下来"二分，而是区分附和、协作、打断三类插入，再判断正在说话的一方是"继续原话"、"边说边适应"还是"让出话轮"（Duplex Cue 的设计，[报道](https://segmentfault.com/a/1190000048300229)）。

#### 2.9.3 ASR WER 与 TTS 主观评测

- **ASR WER** 是标准客观指标，但报 WER 时必须同时报**域与条件**（方言、口音、噪声、远场），否则不可比。Full-Duplex-Bench 的工程总结明确列出"样本偏差：测试数据可能无法覆盖所有真实场景（如方言、特殊口音），需持续补充数据集；环境差异：硬件、网络延迟会影响复现，建议固定测试环境"（[Full-Duplex-Bench 解析](http://developer.baidu.com/article/detail.html?id=7367015)）。
- **TTS 主观评测用 MOS（Mean Opinion Score）**：ITU-T P.800 标准化，5 分制，最常用的是 ACR（绝对等级评分）；另有 P.830（针对 TTS 的噪声条件下评测）、P.835（同时评自然度与可懂度）。客观指标可用 P.862/PESQ，但频谱层面的"准确"不等于听觉层面的"舒适"——两个频谱高度相似的音频听起来仍可能机械生硬（ITU-T 建议书说明见 [MOS 方法综述](https://blog.csdn.net/weixin_42594427/article/details/156498735) 与 [Cartesia MOS 说明](https://www.cartesia.ai/learn/mean-opinion-score)）。行业惯例是 MOS ≥ 4.0 视为接近真人。
- **自然度的自动评测仍是开放问题**：全双工口语对话的话轮自然度"对系统至关重要，但其自动评测仍然有限"，已有工作用受控扰动基准（成对的自然/扰动音频）+ 人工自然度判断来验证（[Moro-Velazquez 等工作](https://www.aminer.cn/academic/scholar/laureano-moro-velazquez/53f43449dabfaee43ec1b48b)）。
- **真人盲测成对投票**是语音 Agent 主观质量的主流做法：VoiceArena 的 Jarvis Bench 把评测拆成 Task Completion 与 Naturalness 两项，由真人实时对话 + 另一组人类盲测成对投票，榜单里还包含一个"人类 Agent"作参照（[VoiceArena Jarvis Bench](https://aihot.virxact.com/items/cmu24lsbc035pro9v2cs5wfqp)）。Artificial Analysis 的 Speech-to-Speech Quality Index 则综合语音推理、智能体任务、Arena 用户偏好、任务成功率四项（[artificialanalysis.ai](https://artificialanalysis.ai/)）。

---

## 三、工具与框架现状（2026）

| 工具 | 定位 | 许可证 | 本地离线可跑 | 适合本项目？ |
|---|---|---|---|---|
| [promptfoo](https://www.promptfoo.dev/) | 声明式（YAML）LLM 评估 + 红队测试框架，断言矩阵 + 多模型并排对比，有官方 GitHub Action | MIT（[GitHub](https://github.com/promptfoo/promptfoo)）；已被 OpenAI 收购（[OpenAI 公告](https://openai.com/index/openai-to-acquire-promptfoo/)），README 声明仍保持开源 | ✅ 全本地（`npx promptfoo eval`），judge 也可指向本地模型 | ⚠️ 可作**参照物**：它的"确定性断言负责底线，模型评分负责语义"分层与本项目 runner 同构；但它是 Node 生态，本项目 runner 已能覆盖同等能力，不构成迁移理由 |
| [OpenAI Evals](https://github.com/openai/evals) | 最早的 eval 注册表框架，含数百个预置基准，支持自定义 eval | MIT | ✅ 代码可本地跑，但多数预置 eval 需要 OpenAI Key | ❌ 不适用：Python 框架但生态绑定 OpenAI；且 2026 年 OpenAI 已把 Evals 产品化到 Dashboard（[Ev 平台文档](https://platform.openai.com/docs/guides/evals)） |
| OpenAI Evals Dashboard / graders | 托管评测平台，grader 分 string_check / text_similarity / score_model(label model) / python 自定义 | 商业托管 | ❌ 需联网与 Key | ❌ 不适用：与"offline 层无网络无 Key"的硬约束冲突 |
| [Azure AI Foundry graders](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/azure-openai-graders) | 同类 grader 体系（string_check / text_similarity / score_model / python），文档最完整 | 商业托管 | ❌ | ⚠️ 只作**grader 分类的参考文档**读，不引入 |
| [Braintrust](https://www.braintrust.dev/docs) | 托管评测/可观测平台，"eval-first"：数据集 → 实验 → 评分器 → 部署门禁 → 生产监控闭环，Loop 智能体辅助生成数据集 | 商业（[定价](https://www.braintrust.dev/pricing)：免费层 1GB 数据 / 10,000 scores，Pro 约 $249/月含 5GB / 50,000 scores） | ❌ 需上传数据到托管 | ❌ 不适用：数据要出本机，且个人项目用不到其核心价值（团队协作 + 生产闭环） |
| [LangSmith](https://docs.langchain.com/langsmith/) | LangChain 系可观测 + 评测平台，trace → dataset → evaluator → experiment 对比，在线评测可配采样率 | 商业（有免费层，可私有化部署） | ❌ 在线评测需联网 | ❌ 不适用：同上；且与框架强绑定叙事 |
| [DeepEval](https://deepeval.com/) | pytest 原生的 LLM 评估框架，Apache-2.0，内置 G-Eval / faithfulness / answer relevancy 等指标，可全本地跑 | Apache-2.0（[GitHub](https://github.com/confident-ai/deepeval)） | ✅ 全本地（judge 模型可指向本地/兼容端点） | ⚠️ 可作为**灵感来源**：它的 `assert_test` 断言式写法与本项目 YAML + checks 同构；若未来要评 RAG 检索质量，可直接借它的 faithfulness / answer relevancy 指标定义而不必引入依赖 |
| [Ragas](https://github.com/explodinggradients/ragas) | RAG 专项评测：faithfulness / answer relevancy / context precision / context recall | Apache-2.0 | ✅ 可本地跑（judge 需模型） | ⚠️ 仅当本项目要做**检索层**指标时参考；其 context precision/recall 需要标注"哪些 chunk 该被检索到"，本项目三档置信度记忆目前没有这种标注 |
| [Langfuse](https://langfuse.com/) | 开源 agent 可观测 + eval，支持 LLM-as-judge、heuristic、人工 review | 核心开源（MIT）+ 商业 EE | ✅ 可自托管 | ❌ 不适用：引入了本项目不需要的 trace 基础设施 |
| [Google AQuA](https://developers.googleblog.com/the-outer-loop-insights-first-an-ambient-quality-agent-that-diagnoses-your-production-agent/) | 生产外循环质量 Agent：从 Cloud Trace/Logging 抽样真实会话 → 评审 → 聚类 → 验证 → 锚定源码快照 | 开源参考实现 | ❌ 依赖 Google Cloud | ⚠️ **方法论值得抄**：它的"HTTP 200 ≠ 任务完成"和"失败聚类回流到开发内循环"正是本项目最缺的一环，但实现绑死 GCP，只能用其流程设计 |
| Google agent-eval toolkit | 与 OpenTelemetry trace 配对的评测管线，支持从 agent instructions + 工具定义自动生成多轮合成场景 | 开源 | 部分 | ⚠️ 合成用例生成的思路可参考（[Google 文档](https://developers.googleblog.com/agent-and-model-evaluations-in-gemini-enterprise-agent-platform-are-now-ga/)） |

**选型结论**：本项目的核心约束（offline 层无网络无 Key、快环内不阻塞、结果只落回复不落 prompt 与凭据、`[情绪]` 标签契约、三档记忆装配）都是**项目特有契约**，没有任何现成框架开箱支持。现有 `scripts/run_evals.py` 已经实现了"YAML 一文件一用例 + 确定性/自定义判定 + 多层 + 多次采样 + 通过规则 + xfail + 落盘"的完整闭环，与 promptfoo 的断言矩阵在设计上同构。**推倒重来是净损失。**

---

## 四、对本项目的适用性（逐条建议）

以下建议按"对上现有实现"的方式写，均不要求推倒重来；标注了预期成本。

### A. 评测集设计

**A1. 引入能力探针 / 回归集二分，但不新建目录。**
现状：9 条用例全是回归性质，`status: active | xfail` 两态。
建议：给 YAML 增加 `kind: regression | capability`（或复用 `layer` 之外的第二个字段）。capability 用例照常运行、单独列一段汇报，**不计入退出码**。这样 `hallu-empty-retrieval-live` 这类"已知有误判风险"的探针可以留在集子里当能力观测，而不会被误当成门禁。
依据：Anthropic 的 capability/regression 二分与"毕业"机制（[链接](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
成本：runner 改约 10 行。

**A2. 用例数量维持 10-30 条量级，优先补维度而不是补条数。**
现状：offline 6 条 + live 3 条。
建议：按"事故驱动"补，不按"凑数"补。当前覆盖到的维度：空检索幻觉、事实优先于人设、情绪标签、三档装配顺序、硬删除、快环非阻塞、lesson 注入。**明显缺失的维度**：多轮指代消解（"那个项目"指哪一个）、负面情绪下的边界、跨天记忆恢复、persona 语气一致性（非事实性）。
依据：Anthropic 早期约 20 条即可起步（[链接](https://www.anthropic.com/engineering/multi-agent-research-system)）。

**A3. 中文用例来源继续走"事故回流 + 人工探针"，不引入公开中文基准。**
现状：`origin` 必填，`docs/TESTING.md` 已规定"每次出幻觉事故就加一条 YAML"。
建议：保持不变。可在 `origin` 里补一个可选字段 `incident:`（事故/实验/commit 的具体编号），让"这条用例为什么存在"可追溯，防止无依据加用例导致的过拟合。
依据：Anthropic 的失败案例回流循环；[arXiv:2508.15503](https://arxiv.org/abs/2508.15503) 要求报告过拟合及其缓解。

**A4. 把 few-shot 示例与评测用例分池。**
现状：`fewshot_emotion_tags` 检查 `demo_turns()`，本身不是评测用例，暂无泄漏。
建议：立一条约定——**任何进入 `demo_turns()` 的文本不得同时作为 live 用例的输入或期望输出**。这是 golden dataset 的独立泄漏类型（[指南](https://qaskills.sh/blog/golden-dataset-llm-evaluation-guide)）。
成本：纯约定，0 代码。

### B. 判定方法

**B1. live 层把"是否编造"从正则升级为 LLM-as-judge（最高优先级）。**
现状：`hallu-empty-retrieval-live` 用 `not_regex`，`accuracy-over-persona-live` 用 custom 正则判定"是否纠正"，`docs/TESTING.md` 自己承认"第一版用正则判断，有误判风险，必须抽查落盘的原始回复"。
建议：新增一个 `check.type: judge`，让一个**与被测模型不同厂商**的模型按 rubric 判"是否声称存在共同经历 / 是否纠正了错误前提"，输出 `pass | fail | review` 三态 + 一句话理由；正则降级为**辅助信号**（judge 与正则不一致时进 `review`，强制人工抽查）。这与 `run_evals.py` 里已有的 `custom` check 完全兼容——只是把 `evals.checks:` 指向一个调用外部 judge 的函数。
依据：判定器优先级"代码 > LLM 裁判 > 人工"（[LangChain FAQ](https://www.langchain.com/blog/common-questions-about-agent-evals)）；MT-Bench 的位置/冗长/自我偏好偏差（[arXiv:2306.05685](https://arxiv.org/abs/2306.05685)）；OpenAI/Azure grader 体系中 score_model 就是干这个的（[Azure 文档](https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/azure-openai-graders)）。
成本：约 60-100 行；只在 `--live` 且配置了 judge Key 时启用，**不破坏 offline 层零 Key 约束**。

**B2. judge 必须做三件去偏。**
1. 交换顺序跑两遍，只在一致时采纳（位置偏差）；
2. rubric 里显式写"回复简洁优于冗长，不得因为长而加分"（冗长偏差）；
3. judge 与被测模型不同厂商，候选在 prompt 里匿名化（自我偏好）。
依据：全部见 [arXiv:2306.05685](https://arxiv.org/abs/2306.05685) 与 [arXiv:2404.04475](https://arxiv.org/abs/2404.04475)。
成本： judge prompt 内完成，0 依赖。

**B3. judge 用二元 / 三态，不用 1-10。**
建议：judge 输出 `pass / fail / review` 而非 5 分制。Anthropic 的结论是单 judge + 0-1 分数 + 通过与不通过与人工判断最一致，多个 judge 并不更准（[链接](https://www.anthropic.com/engineering/multi-agent-research-system)）；工程共识是模型分不清 7 分和 8 分。
成本：0。

**B4. 保留"抽查落盘原始回复"的硬规定。**
现状：`evals/results/` 只存回复与判定，不存 prompt 与凭据。
建议：**不要**因为引入 judge 就放松人工抽查。Anthropic 记录过人工测试者发现自动评测整体遗漏的系统性偏差（偏好 SEO 内容农场），这是人工层不可替代的一手证据（[链接](https://www.anthropic.com/engineering/multi-agent-research-system)）。
成本：0。

### C. 分层架构与 flakiness

**C1. 保持 offline 进 CI、live 不进 CI 的切分，这是当前实现最正确的部分。**
现状：offline 失败即非零；live 无 Key / Mock 跳过且退出码 0。
建议：不变。可补一条：**live 层的跳过必须在输出里显式出现"跳过 N 条 + 原因"**（已做到），且 `run_gates.py` 的"跳过即失败"口径只适用于 offline 门禁，不要让 live 层被 run_gates 误判。
成本：0-5 行。

**C2. 把 pass^k 与 mean@k 分开呈现。**
现状：`pass_rule: all`（= pass^k）与 `min_rate:0.8`（均值阈值）混用，输出只有 `passed_runs/total_runs`。
建议：通过率表加两列——**最低单轮通过率**（≈ pass^k）与**平均通过率**（mean@k）。这样"3 次全过"与"3 次过 2 次"在 `min_rate:0.8` 下都算通过，但报表上能区分"稳定"与"勉强"。
依据：`pass^k ≤ mean@k ≤ pass@k` 与"pass@k 高但 pass^k 低说明有能力但不稳定"（[Anthropic](https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents)）。
成本：runner 约 15 行。

**C3. 记录"跨运行漂移"而不是只记录当次结果。**
现状：每次运行写一个带 UTC 时间戳的独立 JSON，无基线对比。
建议：新增一个轻量 `evals/results/latest.json`（符号链接或覆盖写），并在输出里打印**与上一次同名用例通过率的差异**（配对比较，不是只报总分）。这是"跑一遍评测集"与"回归测试"的分界。
依据：回归测试三要素（可复现基线、逐用例配对比较、统计判据）见 [CI 门禁实践](https://blog.csdn.net/iamqianrenzhan/article/details/168452009)；AQuA 的"失败聚类 + 版本锚定"是同思路的生产版（[Google](https://developers.googleblog.com/the-outer-loop-insights-first-an-ambient-quality-agent-that-diagnoses-your-production-agent/)）。
成本：约 30 行。

**C4. 非确定性显式记账。**
现状：live 落 `finish_reason / rescued / completion_tokens`。
建议：补 `latency_ms`（per-turn 墙钟）与 `judge_*` 字段（若引入 judge），并记录被测模型标识。理由：即使 temperature=0，多卡并行 / 批切分 / MoE 路由下输出仍非逐位确定，换 Provider 量化版本会差出几个 case——不记模型标识的对比不可复现。
成本：约 10 行。

### D. 报告格式

**D1. 报告五块全部补齐，其中缺三块。**
现状已有：通过率表、失败 check detail、xfail 与跳过原因、落盘原始回复（不落 prompt 与凭据）。
缺口：**趋势/回归对比**（C3）、**成本与延迟**（C4 + 费用估算）、**能力探针独立段**（A1）。
建议：报告结构改为：`=== 通过率表 ===` → `=== 与上次差异 ===` → `=== 能力探针（不计入） ===` → `=== 成本与延迟 ===` → `=== xfail / 跳过 ===`。
成本：约 50 行。

**D2. 报告不含 prompt 与凭据的红线保留。**
现状已做到。建议在 runner 里加一条注释级别的自检：落盘前对 JSON 做一次 key 黑名单扫描（`prompt / api_key / messages`），防止将来加字段时误落。
成本：约 10 行。

### E. 语音 Agent 指标（本项目当前完全缺失的一块）

**E1. 分段延迟指标优先于任何"平均延迟"。**
现状：仓库有 `docs/LATENCY_BASELINE.md` 与 `test_speech_performance.py` 等，但 evals 里没有延迟类用例。
建议：新增 offline 延迟用例（用假 TTS / 假 ASR，事件顺序断言而非墙钟——`fastloop-nonblocking-offline` 已经是这个模式，可直接复用），把延迟拆成四段：判停（algorithm_processing）、ASR 尾词（asr_ttlw）、LLM 首 token（llm_ttft）、LLM 首句（llm_ftfs）。其中前两段在 Mock 文本轮里没有对应量、不可离线表达，只能归 live 语音路径。
依据：[声网 Convo AI 官方指标定义](https://doc.shengwang.cn/doc/convoai/restful/convoai/operations/get-turns)。
成本：约 100 行 + 假 provider 夹具。

**E2. 打断/轮次切换用"事件时间线"验收，不用"平均响应时间"。**
建议：用例结构扩成 `events:` 时间线 + `expect:` 期望动作（继续 / 让出话轮 / 保持沉默 / 边说边适应），判定用事件顺序而非耗时。这与 `fastloop_nonblocking` 用事件序断言不用墙钟的做法一致，也与 Full-Duplex-Bench / Duplex-MPE 的指标设计同构（[arXiv:2503.04721](https://arxiv.org/abs/2503.04721)、[arXiv:2609.31948](https://arxiv.org/abs/2609.31948)）。
成本：约 150 行。

**E3. TTS/ASR 主观评测不进自动化门禁，另立人工盲测流程。**
建议：MOS（ITU-T P.800 ACR，5 分制）作为人工抽查的评分表，不进 CI；自然度自动评测目前仍是开放问题，不要为它造指标。
依据：[MOS 方法](https://blog.csdn.net/weixin_42594427/article/details/156498735)、[Cartesia MOS 说明](https://www.cartesia.ai/learn/mean-opinion-score)、[Moro-Velazquez 等关于话轮自然度自动评测受限的工作](https://www.aminer.cn/academic/scholar/laureano-moro-velazquez/53f43449dabfaee43ec1b48b)。
成本：一份评分表 + 约定。

**E4. WER 报的时候必须带域与条件。**
建议：若引入 ASR 评测，用例必须声明 `condition:`（安静 / 噪声 / 远场 / 方言），不同条件分开报，不混一个总数。
依据：Full-Duplex-Bench 工程总结对样本偏差与环境差异的警告（[解析](http://developer.baidu.com/article/detail.html?id=7367015)）。
成本：YAML 字段约定。

### F. 明确不建议做的事

1. **不要迁移到 promptfoo / Braintrust / LangSmith / DeepEval。** 理由见 §三选型结论；尤其 Braintrust / LangSmith 需要把数据上传到托管，与"结果只落回复不落 prompt"的本机约束冲突。
2. **不要给 live 层加"每次 PR 全量跑"。** 花钱、慢、flaky；最多每次 PR 跑 1-2 条冒烟 × 1 次采样，并设预算上限。
3. **不要把能力探针接进 CI 门禁。** 通过率天然低，会长期误触。
4. **不要用 1-10 分量表做 judge。** 用二元/三态。
5. **不要为了刷通过率而调 Prompt。** 用例来源必须可追溯（A3），被刷穿的用例要退役。

---

## 五、Sources

**第一方工程博客 / 官方文档**

1. Anthropic — Demystifying evals for AI agents（2026-01-09）：<https://www.anthropic.com/engineering/demystifying-evals-for-ai-agents>
2. Anthropic — How we built our multi-agent research system：<https://www.anthropic.com/engineering/multi-agent-research-system>
3. LangChain — Agent Evals 常见问题（FAQ）：<https://www.langchain.com/blog/common-questions-about-agent-evals>
4. LangSmith 官方文档：<https://docs.langchain.com/langsmith/>
5. promptfoo 官方文档（断言与指标）：<https://www.promptfoo.dev/docs/configuration/expected-outputs/>
6. promptfoo 仓库（MIT）：<https://github.com/promptfoo/promptfoo>
7. OpenAI — 收购 promptfoo 公告：<https://openai.com/index/openai-to-acquire-promptfoo/>
8. OpenAI Evals 仓库（MIT）：<https://github.com/openai/evals>
9. OpenAI Evals 平台文档：<https://platform.openai.com/docs/guides/evals>
10. Azure AI Foundry — graders（string_check / text_similarity / score_model / python）：<https://learn.microsoft.com/en-us/azure/ai-foundry/concepts/evaluation-evaluators/azure-openai-graders>
11. Braintrust 文档与定价：<https://www.braintrust.dev/docs> 、<https://www.braintrust.dev/pricing>
12. DeepEval：<https://deepeval.com/> 、仓库 <https://github.com/confident-ai/deepeval>
13. Ragas 仓库（Apache-2.0）：<https://github.com/explodinggradients/ragas>
14. Langfuse：<https://langfuse.com/>
15. Google Developers Blog — The Outer Loop, Insights First: An Ambient Quality Agent That Diagnoses Your Production Agent（AQuA，2026-10-08）：<https://developers.googleblog.com/the-outer-loop-insights-first-an-ambient-quality-agent-that-diagnoses-your-production-agent/>
16. Google — Agent and Model Evaluations in Gemini Enterprise Agent Platform（合成场景生成）：<https://developers.googleblog.com/agent-and-model-evaluations-in-gemini-enterprise-agent-platform-are-now-ga/>
17. 声网 Convo AI — 延迟指标定义（algorithm_processing / asr_ttlw / llm_ttft / llm_ftfs）：<https://doc.shengwang.cn/doc/convoai/restful/convoai/operations/get-turns>
18. Artificial Analysis — Speech-to-Speech Quality Index：<https://artificialanalysis.ai/>
19. Cartesia — What is a mean opinion score (MOS)：<https://www.cartesia.ai/learn/mean-opinion-score>

**arXiv 论文**

20. Zheng et al. — Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena：<https://arxiv.org/abs/2306.05685>
21. Dubois et al. — Length-Controlled AlpacaEval: A Simple Way to Debias Automatic Evaluators：<https://arxiv.org/abs/2404.04475>
22. Tan et al. — JudgeBench: A Benchmark for Evaluating LLM-based Judges：<https://arxiv.org/abs/2410.12784>
23. Guidelines for Empirical Studies in Software Engineering involving Large Language Models（明确要求报告数据泄漏/污染/过拟合及其缓解）：<https://arxiv.org/abs/2508.15503>
24. A Comprehensive Survey of Contamination Detection Methods in Large Language Models（v3 原名为 How Much are Large Language Models Contaminated? A Comprehensive Survey and the LLMSanitize Library）：<https://arxiv.org/abs/2404.00699>
25. How Much Can We Forget About Data Contamination?：<https://arxiv.org/abs/2410.03249>
26. Training on the Benchmark Is Not All You Need：<https://arxiv.org/abs/2409.01790>
27. Huang et al. — C-Eval: A Multi-Level Multi-Discipline Chinese Evaluation Suite for Foundation Models：<https://arxiv.org/abs/2305.08322>
28. Li et al. — CMMLU: Measuring massive multitask language understanding in Chinese：<https://arxiv.org/abs/2306.09212>
29. Lin et al. — Full-Duplex-Bench: A Benchmark to Evaluate Full-duplex Spoken Dialogue Models on Turn-taking Capabilities：<https://arxiv.org/abs/2503.04721>（代码：<https://github.com/DanielLin94144/Full-Duplex-Bench>）
30. Full-duplex-bench-v2: A multi-turn evaluation framework for duplex dialogue systems with an automated examiner：<https://arxiv.org/abs/2510.07838>
31. Ray et al. — τ-Voice: Benchmarking Full-Duplex Voice Agents on Real-World Domains：<https://arxiv.org/abs/2603.13686>
32. Duplex-MPE: A benchmark for multi-party full-duplex spoken interaction：<https://arxiv.org/abs/2609.31948>
33. VoxRole: A Comprehensive Benchmark for Evaluating Speech-Based Role-Playing Agents：<https://arxiv.org/abs/2509.03940>
34. Voice Evaluation of Reasoning Ability: Diagnosing the Modality-Induced Performance Gap：<https://arxiv.org/abs/2509.26542>
35. Flexi: Benchmarking full-duplex human-LLM speech interaction：<https://arxiv.org/abs/2509.22243>

**工程实践整理（用于定位一手来源，未作为唯一依据）**

36. Golden Dataset for LLM Evaluation 指南（few-shot 泄漏 / 微调泄漏类型学）：<https://qaskills.sh/blog/golden-dataset-llm-evaluation-guide>
37. LLM 应用 CI 门禁：Prompt 回归测试与显著性判定（固定版本 + 固定种子 + k 次重复取分布）：<https://blog.csdn.net/iamqianrenzhan/article/details/168452009>
38. 全双工语音 Agent 评测：从首音延迟到事件级验收（指标分段定义引自官方文档）：<https://blog.csdn.net/w776341482/article/details/163627068>
39. Full-Duplex-Bench 深度解析（样本偏差 / 环境差异 / 长期运行不确定性）：<http://developer.baidu.com/article/detail.html?id=7367015>
40. LLM-as-a-judge 评估指南（对一手论文偏差类型的整理）：<https://blog.csdn.net/2501_90556106/article/details/168755397>
41. VoiceArena Jarvis Bench v0.5（任务完成度 + 自然度 + 真人盲测成对投票）：<https://aihot.virxact.com/items/cmu24lsbc035pro9v2cs5wfqp>
42. 阶跃 / Besimple AI 日报（Duplex Cue 把听者意图与说话者反应拆开评）：<https://segmentfault.com/a/1190000048300229>

---

调研日期：2026-10-24。本文件为调研记录，未改动任何代码或配置；文中所有"建议"均为提案，需经项目角色流程确认后方可实施。
