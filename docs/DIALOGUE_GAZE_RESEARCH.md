# 对话眼神与表情时序：来源与实现依据

研究日期：2026-10-14（Asia/Shanghai）。本轮目标是让现有 VRM 的注视与眉眼通道跟随「倾听 → 思考 → 发声 → 恢复倾听」，优先参考成熟系统公开的原始机制。这里记录设计依据，不代表当前模型已经完成视觉验收或用户研究。

本轮采用的方向是：倾听时稳定关注用户；思考时临时移视并回看；以实际发声边界切换；打断时撤销旧动作时间线。说话中的停顿移视留待真实语音时间信息可用后实现。

## 1. 可复核的原始来源

以下读取来自官方公开仓库，不是二手营销文章。证据固定到提交，避免后续主分支变化影响核对：

- FurhatRobotics/example-skills：`59b4b4e9ec86f00f9051d7e668b01b04546fd6f7`。
- ArticulatedSocialAgentsPlatform/AsapRealizer：`3fe04c64f4487ff5895c21031ad49c171f7ad8cd`。

来源片段、行号、完整源文件哈希、Git blob SHA、请求状态保存于 [证据目录](../eval/research/dialogue-gaze/2026-10-14/README.md)；机器可读索引为 [sources.json](../eval/research/dialogue-gaze/2026-10-14/sources.json)。保存的是必要源码摘录，没有复制整个外部仓库，也没有将外部源码加入项目运行代码。

| 来源 | 实际观察到的机制 | 能支持的设计结论 |
| --- | --- | --- |
| [Furhat attendingUsers.kt](https://github.com/FurhatRobotics/example-skills/blob/59b4b4e9ec86f00f9051d7e668b01b04546fd6f7/demo-skill/src/main/kotlin/furhatos/app/demo/flow/autoBehavior/attendingUsers.kt) | 进入时关注用户；围绕当前用户头部目标做小范围微运动；`SenseSpeechStart` 事件立即调用 `attend`。示例包含多人注意分配。 | 以用户为倾听目标，保留低幅微运动；用户开始讲话时应及时恢复关注。多人切换不是本项目本轮范围。 |
| [Furhat OpenAIChat/chat.kt](https://github.com/FurhatRobotics/example-skills/blob/59b4b4e9ec86f00f9051d7e668b01b04546fd6f7/OpenAIChat/src/main/kotlin/furhatos/app/openaichat/flow/chatbot/chat.kt) | 收到回答后先 `GazeAversion(2.0)`，再取模型回答、`say`；重新进入时 `listen`。 | 认知等待可以通过阶段触发的移视表达，回复与倾听转换可由真实对话事件驱动。该例子没有证明每次思考必须移视 2 秒。 |
| [Furhat OpenAIChat/utils.kt](https://github.com/FurhatRobotics/example-skills/blob/59b4b4e9ec86f00f9051d7e668b01b04546fd6f7/OpenAIChat/src/main/kotlin/furhatos/app/openaichat/utils/utils.kt) | `GazeAversion` 从四个上下/左右组合中选择方向，使用有限时长的 `frame`，并在 `reset(dur)` 返回。 | 移视是有返回段的临时动作，可以有方向变化，但不需要持续随机游移。ARKit 的 0.5 权重不能直接等同于本项目的注视角度或 VRM 权重。 |
| [Furhat OpenAIChat/parent.kt](https://github.com/FurhatRobotics/example-skills/blob/59b4b4e9ec86f00f9051d7e668b01b04546fd6f7/OpenAIChat/src/main/kotlin/furhatos/app/openaichat/flow/parent.kt) | 注释说明通过移视避免持续盯视；读取 `ActionLipSync` 内部 `_s` 静音音素，排除首尾静音，在静音开始前 0.2 秒发起移视，持续 0.2 秒加静音长度。 | 说话过程的移视适合与真实语音停顿同步。该机制依赖音素时间；文字标点不等于实际静音。本轮只使用发声开始/结束等可靠边界，不伪造停顿时间。 |
| [Asap GazeTMU.java](https://github.com/ArticulatedSocialAgentsPlatform/AsapRealizer/blob/3fe04c64f4487ff5895c21031ad49c171f7ad8cd/Engines/Animation/AsapAnimationEngine/src/asap/animationengine/gaze/GazeTMU.java) | 注视动作有 `ready`、`relax` 等时点，放松段创建到 `RestGaze` 的过渡；`gracefullInterrupt` 把放松时间设置为现在，结束时间按返回默认注视所需时长计算。 | 将进入、停留、回看分开；打断应取消旧时间线，从当前姿态平滑恢复默认目标，不能等待旧动作自行结束。 |
| [Asap GazeShiftTMU.java](https://github.com/ArticulatedSocialAgentsPlatform/AsapRealizer/blob/3fe04c64f4487ff5895c21031ad49c171f7ad8cd/Engines/Animation/AsapAnimationEngine/src/asap/animationengine/gaze/GazeShiftTMU.java) | 注释原话为 “ends with setting the new rest pose gaze”；`stopUnit` 调用 `setRestGaze(restGaze)`。 | 默认注视目标的持久改变，与临时移视是不同动作。思考不应永久改掉用户目标，回看应有明确终点。 |

这些源码可以证明机制与示例写法，不能证明其在当前二次元人物上必然自然，也不能把 SDK 示例参数当成人类行为的普适统计量。

## 2. 本项目采用的设计原则

### 稳定的默认目标与临时偏移

倾听默认关注用户方向，原有微眼跳作为轻微变化叠加。思考移视是临时偏移，有有限停留和回看，不永久更换默认目标。没有人脸追踪的场景中，「用户方向」是约定的屏幕/镜头方向，并不等于测得用户真实位置。

### 由真实状态触发动作

用户讲话、等待回复、音频开始、音频结束和打断是不同事件。模型开始返回文字并不等于音频已经播放：如果文本已经到达但音频还未起播，可以进入回看准备阶段；实际发声状态仍以播放器/音频调度边界为准。没有语音的文字回复应走明确的文字回应路径，避免永久停在思考态。

### 回看与打断优先于旧动作

思考移视必须能被新状态取消。用户打断、音频失败、回复取消时，不执行过期的延迟移视或回看任务；新状态从当前姿态开始过渡。注视应与现有手势、表情和参数优先级协调，不能在最终聚合后覆盖高优先级动作。

### 说话暂停移视等待真实时间信息

Furhat 的停顿移视依赖 `_s` 音素的实际开始/结束时间。本轮不由逗号、句号猜测暂停，也不将随机定时移动描述为与语音停顿同步。未来若音素、对齐后的静音段或可信音频活动信息可用，再加入受预算限制的短暂移视。

### 先验证时序，再扩充表情

第一轮主要验证倾听、思考、回看与发声的衔接，保留现有独立眉毛、下睑、笑眼、眨眼和口型通道的所有权。后续可基于开心、害羞、疑惑等表情分别设定动作进入、保持和退出节奏；不能仅靠增加眉眼权重代替时序。

## 3. 本项目试调参数：不是来源结论

以下仅是起始候选，需要实际渲染与连续互动验证。来源没有证明这些值最优；最终运行值与验证记录应在实现文档中另行保存。

| 参数候选 | 初始建议 | 试调目的 |
| --- | --- | --- |
| 首次思考移视延迟 | 约 0.35 秒 | 快速回复时跳过移视，避免短暂来回闪动。 |
| 单次思考移视停留 | 约 0.6–1.2 秒 | 让动作可读，同时避免等待较久时一直看向别处。 |
| 回看过渡 | 约 0.18–0.30 秒 | 从当前眼位平滑恢复默认目标，发声开始和打断时能及时回看。 |
| 倾听微运动 | 比思考低幅、低频，幅度待测 | 保持关注感，不让随机运动淹没倾听状态。 |

Furhat 示例的 2 秒、0.2 秒、最短 0.2 秒和 ARKit 0.5 权重是该例子的具体实现值。本项目未把它们当作经过验证的最佳时间或跨模型通用幅度。

## 4. 验证时应覆盖的情况

- 极速回复：移视尚未开始就回到回应，不出现一帧闪跳。
- 较长等待：一次移视后能回看，不长时间看向侧面或周期性机械重复。
- 音频延迟：文字先到，发声态仍与实际音频开始一致；音频间隙不会误触发新思考动作。
- 发声结束、播放失败与无语音回复：均能退出回应过程，恢复可解释的空闲/倾听状态。
- 用户打断：旧时间线被撤销，过期动作不会再次拉走眼神。
- 叠加眉眼预设、眨眼、口型和高优先级手势：动作不争抢、没有参数泄漏。

源码研究并不代替这些本地验证，也不代替用户对人物风格的主观判断。

## 5. 获取限制与论文引用边界

本次尝试读取 `docs.furhat.io` 的 flow、gestures、users、speech、attention 页面，正式证据请求均返回 403。BML ReadTheDocs 初次请求返回 403，正式采集重试返回 404；均未读到正文。已采用官方 GitHub 原始源码作为可读替代，不写成「已查阅完整官方文档」。六个固定提交源文件通过 GitHub API 实际返回 200；固定提交的网页请求出现连接关闭/超时，不能写成这些网页均已成功打开。首次研究时两个主分支源码网页返回 200，但它们不替代固定提交的证据。请求状态保留在证据索引中，之后重跑采集若结果变化，应以新证据时间区分。

Crossref 元数据确认 HRI 2014 的原论文 [Conversational gaze aversion for humanlike robots](https://doi.org/10.1145/2559636.2559666)，DOI 为 `10.1145/2559636.2559666`。本次未获得或阅读该论文全文，因此不引用其受试者结果、最佳时长、自然度提升幅度或因果结论。论文可作为后续深入研究入口，不能作为本轮实现效果已经成立的证据。

Asap 源码采用 LGPL 3.0 或更新版本，摘录保留原来源与署名；本轮借鉴行为机制，没有将外部 Java/Kotlin 实现复制到项目运行代码中。
