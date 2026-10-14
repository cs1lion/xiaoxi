# 阿栖被呼唤进来以后，怎样做到可胜任

2026-10-14，Asia/Shanghai。研究依据为当前工作目录源码、隔离离线探针、已有真实看稿评测，以及实际读取的应用接入官方文档。本次只新增研究材料，没有改产品代码、启动或重启服务、读取用户数据库、操作用户软件或新增付费模型调用。工作目录已有大量未提交修改，本文不将 Git HEAD 当作当前产品版本。

**结论：全局呼唤可以是统一入口，胜任承诺必须落到具体任务和真实接入条件。** 不能保证任意软件、任意请求都成功。可以把支持范围内的工作做到：拿到正确对象与足够材料，理解目标，使用实际可用的操作，核对效果，准确交付。必要条件检查不能保证剩余语义判断正确；真实任务评测与用户试用仍不可替代。

**用户需要的保证是结果和负担，而不是模型自报有信心。** 四个判断同时成立才值得扩大：对象与目标正确；帮助有依据且符合用户目的；建议、修改、保存各有相符的实际状态；用户解释、检查和返工的总劳动低于直接使用原工具。准确指出缺条件是负责任的交付，但未完成不能算任务成功。

## 当前证据与实际缺口

| 已核查内容 | 证据 | 能说明什么 |
| --- | --- | --- |
| 通用工具共 11 个 | `server/assistant/service.py:50` | 任务、提醒、笔记只读、事项只读、运行回执；笔记编辑走另外的领域提案链。没有 PPT、浏览器或桌面操作工具。 |
| 对象与版本绑定 | `server/interaction/activity.py:135`、`server/assistant/service.py:204` | 可以沿用捕获的已保存对象，标记新版及过时状态；不能据此宣称已接入外部软件。 |
| 唯一局部替换与保留不可见尾部 | `server/assistant/note_conversation.py:115` | 修改原文必须唯一匹配，不能重叠；可执行性检查不证明改写质量。 |
| 预览、确认、原版本保存 | `server/assistant/note_proposals.py:155` | 预览不写入；确认针对固定原版本；冲突停止；实际写入之后才记录成果。 |
| 看稿观察与讨论 | `server/assistant/editor_insights.py:66`、`:210`、`:354` | 只看正文前 4000 字；引用定位校验不等于观点正确。讨论不编辑、不保存，不是已完成的共创闭环。 |
| 看稿可用性检查 | `server/assistant/editor_insights.py:140` | 检查开关和离线状态，没有当前应用、读写范围、目标定位或任务验收能力的检查。 |
| 通用运行结束 | `server/assistant/loop.py:167` | 合法 final 文本可结束为 completed；没有强制目标达成证据。 |
| 外部软件接入 | 产品目录检索与入口审查 | 当前是 FastAPI + 网页端，没有全局快捷键、桌面壳、浏览器扩展、UIA 或 PPT 适配器。Playwright 是测试工具，共享音频不等于屏幕理解。 |

四组隔离合同测试共 **89 项通过**：`scripts.test_note_conversation`、`scripts.test_note_proposals`、`scripts.test_agent_loop`、`scripts.test_editor_insights`。使用临时数据库、注入模型和假音频，证明程序边界，不证明实际模型质量或任意软件能力。

**自然口语的接入已经有可复核缺口。** 创建并选定一份临时演示稿后：

| 输入 | `AssistantService.should_route` | 活动上下文带稿件正文 |
| --- | --- | --- |
| 第二段太绕了，但这个例子保留。 | false | false |
| 这页太啰嗦了，帮我想想怎么改。 | false | false |
| 把第二段改自然一点。 | true | false |

前两句没有进入行动/笔记专用路由，不代表整个聊天拒绝服务。第三句进入笔记专用链，会另行读取选定版本，因此不能把活动上下文没带正文解释成专用链没有读稿。结果说明：上一轮讨论的口语体验是产品目标，当前入口仍依赖措辞；增加几个关键词不足以证明自然指代已经可靠。

另一条隔离反例：让假模型直接返回“已保存到 PowerPoint。”，没有任何工具，`AgentLoop` 仍返回 `completed`、`receipts=[]`。这是结构上缺少完成证据门槛的反例，**不是实际模型的失败率或幻觉率**。

**已有真模型评测暴露了输出可用性问题。** [结果记录](../eval/editor_quality/20261013T232937641567Z/results.json)始于北京时间 2026-10-14 07:29，使用 `step-3.7-flash`、原创合成稿件和实际看稿服务路径；没有用户稿件、生产数据库或音频调用。

| 指标 | 此次记录 |
| --- | --- |
| 供应商请求/响应 | 15/15 |
| 初始看稿 | 9 次：3 条协议有效观点，6 次无效 JSON |
| 六次失效的直接证据 | 正文为空、`finish_reason=length` |
| 续聊 | 6 个协议有效回复 |
| 有效 JSON 安静判断 | 0 次 |
| 独立语义评分 | 原记录为 `pending_independent_review` |

六次无效分析被服务降级成 `quiet / analysis_unavailable`，不能计作正确安静；观点和回复合法也不能计作语义合格。空输出需要先排查输出预算、端点行为和解析交付，不先认定提高 token 上限一定解决。原始记录的服务、题库和 rubric 哈希与本次读取源码一致；runner 哈希不同，不能宣称当前 runner 与当时执行完全相同。快照摘要和哈希见[研究证据](../eval/research/call_in_competence_20261014.json)。

## 可靠接入的次序

**先固化原目标，再展示浮层。** 快捷键回调先记录原窗口/进程，或由浏览器扩展记录 tab、frame、文档与选区；采集遵循用户已选择的共享范围。浮层出现并抢焦点后，原来的“当前窗口”和选区可能改变。没有取得正文时只持有对象标识，不伪装已经看过。

**优先取得结构化材料，截图作为范围有限的来源。** 应用原生对象/API能给稳定对象与读写能力；浏览器扩展能取得选区和实际加载的 DOM；UI Automation 能读取应用公开的控件模式。截图只描述当时可见画面，不提供完整文档、保存版本或可编辑对象。这是接入选择，不是认定某种方式在所有任务上一定更可靠。

| 来源与实际读取内容 | 设计上的具体影响 |
| --- | --- |
| [Electron globalShortcut](https://www.electronjs.org/docs/latest/api/global-shortcut)：无焦点也可触发，但已占用快捷键会静默注册失败 | 启动时检查注册结果；提供可改键位和托盘入口。快捷键存在不等于捕获到业务上下文。 |
| [Electron desktopCapturer](https://www.electronjs.org/docs/latest/api/desktop-capturer)：枚举可捕获 screen/window 并提供画面采集 | 桌面壳可承接入口和画面，不能自动得到稿件结构或写权限。这里只是技术候选，不是决定迁移到 Electron。 |
| [Microsoft UIA](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-overview)及[TextPattern](https://learn.microsoft.com/en-us/dotnet/framework/ui-automation/ui-automation-textpattern-overview)：能力依赖应用公开的模式；TextPattern 本身不提供插入/修改 | 按控件检测能力；写入另用 ValuePattern、应用 API 或输入动作；文本变化后旧 range 可能失效，需要重新定位。 |
| [MDN activeTab](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/activeTab_permission)：授权来自扩展按钮、菜单或扩展快捷键等用户动作 | 桌面快捷键本身不自动获得浏览器 activeTab。内置 PDF viewer 等受限页面不能按普通文章注入；同源刷新后权限仍在也不代表文档未变。 |
| [MDN executeScript](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/scripting/executeScript)：需要 scripting 和适用页面权限，返回关联 frame/document 的结果 | 绑定 `tabId/frameId/documentId`；脚本内返回结构化错误与观测结果，执行后再读目标状态。Promise 返回不等于业务成功。 |
| [PowerPoint Selection](https://learn.microsoft.com/en-us/office/vba/api/powerpoint.selection)：可取得 ShapeRange/SlideRange/TextRange；切换幻灯片会删除 Selection | 若以后做 Windows PPT，先限制可支持对象类型，绑定演示文件、slide/shape 和原文本；跨轮不能盲用旧 Selection。 |

浏览器研究实际读取 MDN 的厂商兼容性说明；Chrome 官方站点访问超时，没有把未读取页面当作依据。以上说明外部技术可以提供什么，均不是本项目已实现或已验证的能力。

## 最小能力链

```text
呼唤并捕获原目标
  → 取得有来源、范围和时点的材料
  → 明确本次目标与必须保留的约束
  → 检查所需读/写/验证能力
  → 生成建议或执行已授权操作
  → 重读目标、核对实际效果
  → 交付真实结果与未完成部分
```

**入口应直接携带对象，不再靠一句话猜“这里”。** 最小上下文包可复用现有对象引用，加入以下字段；这是提案，不是新增运行时协议：

```text
source_kind: saved_note | editor_draft | browser_selection | app_object | screenshot
source_identity: 应用/文档/便笺/标签页/编辑器 session 的真实身份
revision: 保存版本、草稿修订或应用能提供的版本/内容摘要
anchor: 当前选区、段落/形状标识或引用原文
content: 已实际取得的选区与必要邻近材料
coverage: selection | partial | complete，并附截断/未加载范围
captured_at: 采集时间
operations: 此刻适配器实际提供的读取、提议、修改、保存能力
verification: 可检查哪些结果，哪些暂时无法验证
```

标识、来源范围和能力来自接入层，网页正文、记忆和模型自报标签不能升级成权限或可信完成证据。用户当前请求决定本次目标；长期画像不代替当前要求。

**预检只消除可以确定的缺口，不负责保证模型聪明。** 检查是否能读到目标、选区是否定位、是否掌握任务所需范围、版本是否仍有效、需要的操作是否存在、结果是否可核对。输出可直接处理、只能提建议、缺一个条件、当前不支持。对缺条件给具体补齐方式，不用一组技术配置问题转嫁给用户；已有授权继续沿用有效范围。

**草稿与保存版必须继续分清。** 现有看稿讨论针对未保存草稿，笔记提案针对已保存版本。“按这个改”不能默默转到同名便笺的旧保存版。草稿的修改建议须绑定 session/revision/内容摘要；应用到草稿、保存到便笺是不同结果。保存版则继续复用固定原版本、唯一局部替换与一次确认机制。首批可先只保证保存版闭环，草稿保留有依据的建议能力。

**保留运行状态，增加业务结果与验证范围。** 不必为了研究迁移或重写现有运行库。`completed`继续描述运行结束，业务结果另说明建议已生成、指定修改已应用、保存已核对、部分完成或不可用，并附对象引用、实际回执、完成的检查与未核对项。不能只靠检测“已保存”几个词补救虚报；成功表达应由真实业务状态支持。

**每类工作要有自己的验收器。** 笔记检查准确新版本、内容、保留段和旧版仍可读；浏览器输入检查目标文档未变、修改值回读及网页自己的保存结果；PPT 检查正确 slide/shape、文本与保存状态，若承诺布局还需渲染核对。来源项目 [OSWorld 的文档比较器](https://github.com/xlang-ai/OSWorld/blob/main/desktop_env/evaluators/metrics/docs.py)实际读取文件比较内容，说明可观察产物检查的做法；具体比较器也有覆盖边界，不能替代全部语义与版式评价。

**生成质量与执行真实性分开验证。** “例子保留”能用原文/差异检查的部分先检查；论证是否成立、改写是否尊重作者目的等，沿用真实稿件 rubric。引用存在只证明定位，保存成功只证明状态变化。没有依据给每次请求都再加判断模型和审核模型；只在失败样本证明需要时增加复核成本。

## 支持范围与实施顺序

| 场景 | 建议承诺 | 当前与新增的边界 |
| --- | --- | --- |
| 本产品已保存便笺 | 对指定版本提出局部改写、预览、保存后接续 | 领域机制已有；先补自然入口和完成核对，真实质量仍需达标。 |
| 本产品编辑草稿 | 可见范围的评析、解释、建议稿 | 已有看稿和续聊；不声称已修改，先解决现有空输出和质量问题。 |
| 普通浏览器文章的选区 | 解释、讨论、基于所读内容起草 | 新增扩展读取适配器；不承诺未加载全文或跨任意站点写入。 |
| 经验证的浏览器文本框 | 指定范围替换及回读 | 后续按页面/编辑器逐项支持；页面值变化不自动代表云端保存。 |
| Windows PPT 所选文本对象 | 修改指定形状文本、核对保存 | 以后单独实现原生适配器；不把截图模式等同于可改原件或保持全部布局。 |
| 任意应用截图 | 对当时可见内容解释与指导 | 新增画面入口后也仅是有范围的辅助能力，不承诺修改原件。 |

建议先做三步：

1. 在已有看稿基线上定位六次空输出，完成观点与续聊的独立语义评分。保留原报告；预算/解析修改和模型变化分开比较，使用预留新题。真实新调用应另设请求和成本边界，本研究没有执行。
2. 在内置便笺闭环验证“显式当前对象 + 自然请求”的入口，让讨论、预览、保存结果能接上。只替换已确认有问题的接缝，保留现有版本和授权服务，不先安装新 SDK 或另建总控运行时。
3. 取得内置场景真实收益后，先扩展浏览器选区读取；再按需要考虑 PPT 及特定写入场景。桌面壳与唤醒词服务入口，不作为能力成熟的证据。

**程序性记忆可以辅助熟悉的做法，但不能替代工具与验收。** [既有实验](PROCEDURAL_MEMORY_EXPERIMENT.md)13 项离线机制检查通过；候选仍是人工/脚本给出的资料，未证明真实模型归纳或迁移收益。做法卡需带前提、需要的实际能力、失败条件和结果来源；本次目标、版本和授权重新建立。不能把保存一段做法直接视为新增可执行能力。

## 怎样证明可以扩大

**先证明一个深场景，评价完整呼唤而不是单轮答案。** 建议准备 24 个支持范围内、预期可交付且未参与调优的任务，覆盖自然口语、指代、保留约束、限定长稿片段、有效/无效反对与内容来源；另选 6 个任务重复运行，观察稳定性。每案预写目标和合格依据，保留失败和实际产物。并发换稿、中断、连接失败、正文诱导等另做故障/边界注入题：正确停止可以算边界通过，不能算目标已交付，也不加入这 30 次交付题的成功分子。这个数量是小范围试用的设计提案，不是统计可靠性证明。

| 记录项 | 判断方式 |
| --- | --- |
| 覆盖率 | 全部真实需求中，有多少落在已验证支持范围；不把不支持任务移出后宣称全局成功。 |
| 端到端合格率 | 从呼唤到成果达成；超时、协议拒收、输出不完整和缺材料均保留在分母。 |
| 正确静默 | 只统计成功分析后作出的安静判断；不可用降级另列。主动求助不能用静默算完成。 |
| 对象与执行真实性 | 错对象写入、越权执行、虚报保存分别记录，任一发生先修复再扩大该路径。 |
| 内容质量 | 沿用[看稿质量标准](EDITOR_QUALITY_RUBRIC.md)：依据、重要性、对作者目的的响应、改写忠实性；程序检查与独立语义评分分开。 |
| 用户负担 | 重讲次数、澄清/纠正轮次、主动操作与检查返工时间；与常用工具同材料同目标对照。 |
| 延迟 | 呼唤到可用上下文、首个有用回应、成果交付的 p50/p95；不能用一句等待语代替有用回应。 |

可把支持范围内明确求助 **至少 27/30 端到端合格**、无关键失真作为进入小范围试用的建议门槛；不是已有结果或面向任意任务的保证。主观质量和操作真实都须达标，诚实未完成不升级成成功。试用还要确认质量不低于现有工具、用户总劳动确实下降；目标可以先设为主动处理时间中位数减少 20%，再由实际基线调整。主动看稿沿用已有更细的安静/介入标准，不能混算成求助成功率。

最现实的产品承诺是：支持范围内，阿栖能把当前这件事接清楚、推进到有依据的成果；缺条件时具体补齐，已发生的效果与尚未完成的部分始终查得清。先兑现这条承诺，再扩大呼唤地点与能力覆盖。

## 可复核离线探针

以下代码由本次审查实际执行，临时目录自动清理，模型为离线占位或注入函数；不读用户数据。可在项目目录用 `.venv/Scripts/python.exe -c` 执行。测试命令为 `.venv/Scripts/python.exe -m unittest scripts.test_note_conversation scripts.test_note_proposals scripts.test_agent_loop scripts.test_editor_insights`。

```python
import asyncio, json, tempfile
from pathlib import Path
from types import SimpleNamespace
from server.assistant.service import AssistantService
from server.assistant.loop import AgentLoop
from server.assistant.runtime import RuntimeStore

with tempfile.TemporaryDirectory(prefix='aqi-capability-probe-') as folder:
    service = AssistantService(Path(folder), SimpleNamespace(offline=True))
    note = service.notes.create('演示稿', '第一段说明背景。第二段原文太绕。第三段总结。')
    service.notes.select(note['id'], version=1, expected_revision=1)
    for text in ['第二段太绕了，但这个例子保留。',
                 '这页太啰嗦了，帮我想想怎么改。', '把第二段改自然一点。']:
        print(json.dumps({'utterance': text,
                          'action_routed': service.should_route(text, 'probe'),
                          'note_body_included': service.activity.context(text)['included_note_body']}))
    service.close()

async def probe():
    runtime = RuntimeStore()
    async def model(payload):
        return {'action': 'final', 'text': '已保存到 PowerPoint。'}
    result = await AgentLoop(model, [], runtime).start('帮我修改并保存当前 PPT')
    print(json.dumps({'unsupported_save_claim': result['text'],
                      'run_status': result['status'], 'receipts': result['receipts']}))
    runtime.close()

asyncio.run(probe())
```
