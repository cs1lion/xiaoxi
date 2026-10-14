# 交流眼神 V3：表达节奏与回合复盘

日期：2026-10-14（Asia/Shanghai）。在[交流眼神 V2](DIALOGUE_GAZE_V2_RESULTS.md)基础上，落实短回答、长思考、多段语音和中途打断的检查入口，增加认真、犹豫、调侃的节奏，以及生成文字和实际播放边界的复盘。

## 使用入口

[实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-lids&dialogue=1#dialoguePanel)：选“眉眼节奏”，再播放眼神流程；新增短回答和多段语音，原有慢回复与打断流程继续保留。实验室是静音口型演示，无法评估外部 TTS 的声音或对齐精度。

[真实聊天](http://127.0.0.1:8000/?avatar=seed-daily-lids)：在“声音与形象”中选择节奏并开启“回合复盘”。默认仍为自然风格、自动随情绪、关闭记录。记录只驻留页面内存；关闭页面即清除，导出需点击按钮。

## 节奏行为

风格控制表现幅度，节奏控制表达时序，两者可以组合。自动模式只映射现有明确情绪：认真→认真节奏、调侃→调侃节奏，其他标签使用自然节奏。犹豫先提供手动选项；没有分析文字、标点或情绪诊断。

| 节奏 | 眼神与眉眼变化 |
| --- | --- |
| 自然 | 保留 V2 的小幅移视、轻抬眉和发声后柔和眼神。 |
| 认真 | 移视幅度更小，每轮最多一次；眉毛更稳，不增加自动笑眼。 |
| 犹豫 | 首次移视前等待更长，实际移视开始 0.32 秒后才起单侧眉；每轮最多两次，不增加自动笑眼。 |
| 调侃 | 每轮最多两次移视；实际发声且已回看后，单侧眉在 0.28 秒起，轻微不对称笑眼在 0.42 秒起。 |

这些时间是本角色的试调值，不能解释为生理测量或经用户研究确认的最佳值。输入通过现有参数平滑、眼头协调与形变映射后才形成最终画面。

| 节奏 | 移视倍率 | 首次等待倍率 | 保持段倍率 | 每轮移视上限 |
| --- | --- | --- | --- | --- |
| 自然 | 1.00 | 1.00 | 1.00 | 4（另受风格上限约束） |
| 认真 | 0.65 | 1.15 | 0.80 | 1 |
| 犹豫 | 0.90 | 1.60 | 1.05 | 2 |
| 调侃 | 0.85 | 1.10 | 0.90 | 2 |

等待开始时固定时间计划。后来变更情绪或节奏可以改变当前幅度和次数上限，不能把已经过去的移视挪到未来重播。三种节奏沿用同一有限随机蓝图；重复标签不产生新话轮。

显式情绪、手势、语篇动作、面部反应与手动预览继续逐键优先，包括显式零值及同侧笑眼/下睑的成对保护。负面、未知情绪，即使手选调侃也不会自动加入笑眼。打断立即取消尚未执行的眉眼变化。

## 复盘的范围

复盘记录输入文字、生成文字、播放片段的开始/结束事件，以及约每 100 毫秒的眼神参数、节奏、表情和控制归属；不记录音频数据、录音、凭据，也不自动上传或写入浏览器存储。最多保留 12 回合，每回合 120 秒、1200 帧，文字与事件另有上限，达到上限会显示截断提示。

- `done` 表示生成结束。真实播放队列空闲后才结束复盘回合；句间等待属于同一回合。
- 只有实际播放器调用 `source.start()` 后记录片段开始。接收到服务器音频或文字不能算作已播放。
- “已开始播放片段”不声称整句已听完，打断可能发生在句中。
- 续播使用独立回合；现有音频偏移没有剩余文本对齐，只标记续播音频，不把原整句重新计入已播放文字。
- 中途开启、停止后再开、清空后继续均创建独立片段。旧输入和累计全文不回填；该片段的 `done` 不带服务器聚合全文。
- 服务器事件先经过现有轮次过滤；已退休消息和迟到解码不能污染新回合。
- 切换模型、校准和断连停止记录。校准退出不会自动再开记录；原交流计划、节奏与媒体时钟身份会恢复。

时间轴用于查看已记录的水平/垂直视线及播放/打断时点，不是音频或动画回放。界面可见时约每 500 毫秒刷新，仅读轻量目录和所选单回合；导出才获取完整快照。所有记录文本通过 `textContent` 显示。

## 接线

| 文件 | 职责 |
| --- | --- |
| [dialogue-intent.js](../web/avatar/drivers/dialogue-intent.js) | 明确节奏配置和自动映射。 |
| [dialogue-gaze.js](../web/avatar/drivers/dialogue-gaze.js) | 固定时序、节奏幅度与次数上限。 |
| [dialogue-expression.js](../web/avatar/drivers/dialogue-expression.js) | 六项眉眼增量与延时，保留显式控制优先级。 |
| [dialogue-review.js](../web/avatar/dialogue-review.js) | 默认关闭的有界页面内存记录，区分生成与播放。 |
| [dialogue-review-ui.js](../web/avatar/dialogue-review-ui.js) | 片段 ID 映射、时点查看、文字和时间轴、清空和导出。 |
| [index.js](../web/avatar/index.js) | 解析当前/镜像情绪节奏，在最终绘制后采样。 |
| [app.js](../web/app.js) | 真实队列播放、完成、取消、编辑器本地播报、续播与断连。 |
| [avatar_lab.js](../web/avatar_lab.js) | 四类情境及每段独立的静音复盘。 |

## 验证

`npm run test:dialogue-gaze`：122 / 122 通过（51 项导演、26 项表达、4 项意图、19 项记录、22 项生产 Avatar 集成）。覆盖冻结时序、实时变更、旧动作不重播、媒体门控、明确零值、认真/犹豫不笑、校准恢复与记录上限。`npm run test:eyes`、`npm run test:calibration`、`npm run test:voice` 原有测试通过。

共享项目同期更新模型渲染、校准与实验室后，在最终版本重新通过[交流 122 项](../eval/avatar/dialogue-gaze-v3-unit-final/dialogue-final.log)、[眉眼 29 项](../eval/avatar/dialogue-gaze-v3-unit-final/eyes-final.log)与[校准 12 项](../eval/avatar/dialogue-gaze-v3-unit-final/calibration-final.log)。本轮接线与同期改动一起保留。

`npm run test:dialogue-gaze:v3:browser`：[87 / 87 浏览器检查](../eval/avatar/dialogue-intent-review/2026-10-14T02-32-04-796Z/results.json)通过，0 页面异常；运行源、配置和模型的哈希前后相同。检查覆盖真实 VRM 形变、意图映射、迟到音频、显式控制保护、记录上限、文字安全、导出、校准/切换模型、生产聊天分句和提前 `done`、中断后的退役消息、独立续播、同回合停录再开与清空后的片段。

[导出样例](../eval/avatar/dialogue-intent-review/2026-10-14T02-32-04-796Z/review-export.json)、[手机实验室](../eval/avatar/dialogue-intent-review/2026-10-14T02-32-04-796Z/mobile-review-controls.png)、[手机聊天复盘](../eval/avatar/dialogue-intent-review/2026-10-14T02-32-04-796Z/mobile-chat-review.png)保留本轮布局和字段证据。聊天检查使用生产 Avatar、VRM 和 PlaybackQueue，替换网络及音频设备；没有测试外部 TTS 或用户录音。部分合同检查固定控制时钟，连续画面另行采集。

原有交流眼神浏览器检查在最终源上重新通过：[V1 56 / 56](../eval/avatar/dialogue-gaze/2026-10-14T02-24-14-789Z/results.json)、[V2 75 / 75](../eval/avatar/dialogue-gaze-v2/2026-10-14T02-24-52-964Z/results.json)，均为 0 页面异常；V2 运行期间源码哈希保持一致。

原有四套[串行回归](../eval/avatar/dialogue-gaze-regressions/2026-10-14T02-25-49-332Z/results.json)通过：眼部 54、校准界面 21、下睑 36、语音 37，0 未捕获页面异常，原测试源码没有改变。该回归完成时[19 项运行来源核对](../eval/avatar/dialogue-gaze-regressions/2026-10-14T02-25-49-332Z/runtime-source-verification.json)与当次 V3 基准一致。最后仅补复盘界面的中文状态名称，在最终版本上重新通过新一轮 87 项验收；其余已验收的驱动、渲染和播放来源没有变化。

## 连续画面

两次独立采集均使用公开控件：选“眉眼节奏”，点“播放眼神流程”，普通 RAF、AudioContext、身体待机与眨眼照常运行；三种节奏使用同一计划种子，未冻结画面，也未人工推进时钟。采集脚本为 [capture-dialogue-gaze-v3-preview.mjs](../scripts/capture-dialogue-gaze-v3-preview.mjs)，拼图脚本为 [compose-dialogue-gaze-v3-preview.py](../scripts/compose-dialogue-gaze-v3-preview.py) 与 [compose-dialogue-gaze-v3-video.py](../scripts/compose-dialogue-gaze-v3-video.py)。

- [9 张实际播放截图、轨迹与复盘界面](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-33-38-231Z/results.json)与[三种节奏眼区对照图](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-33-38-231Z/contact-sheet.png)。每种节奏保留倾听、实际移视后、发声阶段三张截图，以及当轮的复盘界面；截图前后核对同一节奏、同一状态相位与同一计划种子。
- [独立连续视频](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/continuous.webm)及[运行来源、轨迹与复盘快照](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/results.json)。该次录制没有并行截图，保持录制视口一致；[抽帧窗口与视频哈希](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/video-strips.json)记录每 0.1 秒取样，这是检查取样频率，不是渲染帧率。

视频抽帧条：[认真 · 移视与回看](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/video-strip-serious-averted.png)、[犹豫 · 移视后眉变化](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/video-strip-hesitant-averted.png)、[调侃 · 移视与回看](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/video-strip-teasing-averted.png)、[调侃 · 发声后的眉眼与控制优先级](../eval/avatar/dialogue-gaze-v3-preview/2026-10-14T02-35-14-126Z/video-strip-teasing-speaking.png)。

两次采集均为 Windows 无头 Chromium 149.0.7827.55，报告 AMD Radeon 780M / D3D11；各自保存本轮运行文件快照与哈希及模型哈希，前后一致，页面异常为 0。

人工复查对照图与视频抽帧可见：认真档眉变化更少，犹豫档在实际移视后起单侧眉，调侃档在回看后带出轻微笑眼；连续帧未见跳到旧移视目标或虹膜漏出。发声期间手势临时接管视线时，交流眉眼增量按预期让出控制。三种节奏的差异都很轻，身体待机与眨眼也会造成不同画面，不能从这组自由运行截图做固定像素比较或证明自然度提升。默认仍建议自然风格加自动节奏，其余节奏供实际交流时比较喜好。

## 当前范围与后续调节

模型仍为既有 `seed-daily-lids.vrm`，11,884,568 字节，SHA256 `16df0347c2c1b6b742f8a5b632b7a022ec65259331d95681c39f2548ddaa2eaa`。下睑仍为近似能力，左右按角色侧；没有新资产或服务器协议。

这一轮先提供可检查的节奏和播放证据。下一步通过四类真实交流选出喜欢的表现，再针对同一情境调整一组数值；目前没有自然度用户研究或新增性能收益测量。句内停顿移视仍等待可信静音段/音素时点，不用标点猜测。
