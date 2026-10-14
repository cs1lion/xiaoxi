# 交流眼神 V2：眉眼时序、有限变化与三档表现

日期：2026-10-14（Asia/Shanghai）。在[第一轮交流眼神](DIALOGUE_GAZE_IMPLEMENTATION.md)基础上，落实用户同意的四项优化：错开眼神和眉眼变化、每轮有限且可复现的移视变化、眼头协调分档，以及实验室三档连续比较。

后续[交流眼神 V3](DIALOGUE_GAZE_V3_RESULTS.md)已加入认真、犹豫、调侃的节奏和回合复盘。本文保留 V2 历史数值与验收证据。

## 体验与落实顺序

[交流眼神实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-lids&dialogue=1#dialoguePanel)默认使用“自然”。可以选择“克制 / 自然 / 活泼”，播放正常、慢回复或中途打断流程；“依次对比三档”播放三段相同流程，各 10.2 秒，总计 30.6 秒。演示用普通 RAF 和 AudioContext 驱动静音口型。

动态观察使用以上入口；`calibration=1` 固定画面用于检查形变。真实聊天入口为[下睑候选](http://127.0.0.1:8000/?avatar=seed-daily-lids)，使用既有语音播放边界，默认自然档。

| 计划 | 已落实的行为 |
| --- | --- |
| 先完善移视计划 | 每轮生成四个有限条目，三档共享同一随机种子的基础计划；重复状态消息不重新抽取。 |
| 再增加眉眼回应 | 倾听轻抬眉、思考移视后稍晚抬眉、真实音频开始后稍晚加入轻量笑眼与下睑。 |
| 接入眼头协调 | 复用现有 IdleDriver，用三档控制头部参与量、反应延时和参与门槛。 |
| 提供可重复对比 | 三档共享文本、音频时长和基础移视计划，待机身体和眨眼照常运行。 |
| 检查所有权和取消 | 显式表情、手势、面部模仿、话语手势和手动预览优先；打断清理旧计划。 |
| 验证真实循环 | 驱动与宿主单测、真实 VRM/聊天浏览器检查、原有回归及独立连续画面采集。 |

## 表现与时间

| 档位 | 眉高倍率 | 笑眼 / 下睑倍率 | 移视倍率 | 头部参与倍率 | 头延时倍率 | 头参与门槛倍率 | 每轮最多移视 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 克制 | 0.55 | 0.65 | 0.72 | 0.40 | 1.20 | 1.00 | 2 |
| 自然 | 1.00 | 1.00 | 1.00 | 0.65 | 1.00 | 0.75 | 3 |
| 活泼 | 1.25 | 1.15 | 1.15 | 0.85 | 0.90 | 0.65 | 4 |

这些倍率作用于现有归一化控制空间，不能解释为真实眼球角度或头部角度。“头部参与倍率”在参与门槛之后生效，并继续受原有容量上限约束；眼部目标与分配给头的目标之和保持为总目标。

- 倾听眉高目标在进入后 0.15 秒开始，0.24 秒渐起，自然档左右峰值约 0.075 / 0.065；长倾听逐渐收至 55%。
- 思考眉高在实际移视段开始后 0.15 秒再起，0.22 秒渐起；依据当前移视方向选择左右 0.060 / 0.025 的目标。回看时平滑退场。
- 回应笑眼在实际音频开始后 0.20 秒再起，0.34 秒渐起；自然档笑眼目标 0.045、下睑目标 0.055。身体、参数平滑与现有表情还会影响最终形变权重。
- 仅 `base / 调侃 / 得意` 允许回应笑意；认真、负面与未知标签保持原有情绪。仅 `seed-lids` 提供下睑增量，旧模型立即清零该增量。
- 减少动态时，眉眼增量使用 0.45 倍，移视使用既有 `reducedMotionScale`。没有新增独立上睑或瞳孔形变。

默认自然档首次移视计划在 0.315–0.385 秒开始，目标保持 0.64–0.92 秒；之后的开始间隔在 5.76–10.4 秒之间变化，方向与幅度也有界变化。自然档最多三次，预算耗尽后持续关注用户，不再固定每八秒重复。目标保持时间不是实际眼球运动时长，最终运动仍由已有眼头驱动和平滑决定。

这组时间是当前角色的试调值，延续[官方源码研究](DIALOGUE_GAZE_RESEARCH.md)中的关注目标、临时移视和中断撤销机制；没有研究证据证明这些具体数值最自然。

## 接线与优先级

`DialogueGazeDirector` 输出目标与上下文；新增 `DialogueExpressionDriver` 只输出六项加法增量：左右眉高、笑眼、下睑。它不写眼开度、眨眼、注视、头、嘴或瞳孔。

Avatar 在合成时逐键保护 emotion、gesture、discourse、face 已经写入的通道，包括明确的零值。同侧 smile 或 lower 任一明确拥有时，两者一起受保护。手动注视、眉眼预览和显式注视所有权暂停交流眉眼增量；后台计划继续推进，归还控制时采用当前相位。

| 文件 | 作用 |
| --- | --- |
| [dialogue-gaze.js](../web/avatar/drivers/dialogue-gaze.js) | 三档、每轮有限计划、可复现种子、音频门控与眼头上下文。 |
| [dialogue-expression.js](../web/avatar/drivers/dialogue-expression.js) | 小幅眉眼增量、错开时序、平滑退场、取消和旧模型降级。 |
| [idle.js](../web/avatar/drivers/idle.js) | 有界头部参与、延时与门槛，保持眼头目标总和。 |
| [index.js](../web/avatar/index.js) | 通道保护、实际合成及独立的交流时间步。 |
| [calibration.js](../web/avatar/calibration.js) | 保存并恢复两个交流驱动、计划、上下文及诊断帧。 |
| [avatar_lab.js](../web/avatar_lab.js)、[avatar_lab.html](../web/avatar_lab.html) | 三档选择、相同计划对比、停止与待启动取消。 |

`setStyle(id)` 不重抽正在进行的计划，不撤销音频片段；`resetVariation(seed)` 只改变下一份计划的种子。`configure({style, seed, ...})` 继续接受原有有界参数，可在 `config/avatar.yaml` 的 `gaze.dialogue` 配置。实验室每段重置同一比较种子，真实交流逐轮推进种子。

诊断保留 `avatar.dialogueFrame.plan / glance / phaseAge` 和 `avatar.dialogueExpressionFrame.values / appliedValues / protectedKeys`；`window.__dialogueComparison` 记录对比是否进行、档位、序号和总 elapsed。

审查修复了两个实际边界：等待 `AudioContext.resume()` 时改选风格会使旧启动失效；RAF 长间隔后的交流计划使用原始 elapsed 跳过过期移视，身体与滤波仍采用不超过 0.05 秒的步长。进入校准后没有推进这段时间，退出重新建立 RAF 起点。

## 验证

`npm run test:dialogue-gaze` 77 / 77 通过，包含 43 项导演、19 项眉眼增量和 15 项生产 Avatar 集成。新宿主回归运行实际 `Avatar._start → _frame`，模拟 2 秒与 60 秒 RAF 间隔，验证过期移视不重播、预算耗尽、身体时间步有界。该检查是宿主时间模拟，不是浏览器后台调度实测。

`npm run test:eyes` 29 项 TAP、`npm run test:calibration` 当前 12 项 TAP 通过，均为 0 失败；眼部 TAP 还包含原生形变和注视脚本的内部断言。校准的新增手型恢复检查来自共享项目同期的手型工作，保留后复查兼容性。

| 浏览器检查 | 结果与证据 |
| --- | --- |
| `npm run test:dialogue-gaze:v2:browser` | [75 / 75，通过后的当前稳定源](../eval/avatar/dialogue-gaze-v2/2026-10-14T01-23-54-046Z/results.json)，0 pageerrors；18 项源/配置/模型/测试哈希前后不变。 |
| `npm run test:dialogue-gaze:browser` | [原有 56 / 56](../eval/avatar/dialogue-gaze/2026-10-14T01-19-16-955Z/results.json)，0 pageerrors。 |
| `npm run test:dialogue-gaze:regressions` | [原有四套串行回归](../eval/avatar/dialogue-gaze-regressions/2026-10-14T01-19-48-295Z/results.json)：眼睛 54、校准 UI 21、下睑 36、语音 37 全部通过；独立输出，原测试脚本哈希保持不变。 |

V2 检查包括真实 VRM 形变、六项显式零值和同侧通道保护、真实 IdleDriver 头部输出、音频播出中改档不撤片段、有限等待预算、校准完整字段恢复、旧模型下睑降级、减少动态、手机布局、待音频恢复时停止或改选、完整 30.6 秒相同基础计划比较，以及生产聊天与 PlaybackQueue 接线。部分合同检查固定 RAF 并推进控制时钟；动态对照另用持续真实播放采集。

真实聊天检查保留生产 Avatar、模型与播放队列，替换网络、麦克风和音频设备；没有实测外部 TTS 或用户录音。回归过程中共享项目继续增加手型预览恢复逻辑，随后在稳定的新源上重新通过上述 V2 75 项，没有回滚同期改动。曾有一次静态模块加载失败并回退 SVG，保留[失败记录](../eval/avatar/dialogue-gaze-v2/2026-10-14T01-15-41-567Z/results.json)；相关资源随后 HTTP 200，重新加载通过。

## 连续画面复查

两次独立采集均播放实验室的完整三档比较，普通 RAF、AudioContext、身体待机与眨眼照常运行；没有固定姿态或人工推进时钟。三档基础移视计划相同，各段进度和实际帧状态均保存在轨迹中。

- [9 张实际播放截图及轨迹](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-27-19-385Z/results.json)与[眼区对照图](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-27-19-385Z/contact-sheet.png)。截图前后核查同一档位与同一关注相位；思考移视期间 `thinking → preparing` 的标签变化允许继续同一动作。
- [独立连续视频](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/continuous.webm)及[运行来源与轨迹](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/results.json)。该次录制没有并行截图，保持录制视口一致。
- 视频取样帧条：[首次移视与回看](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/video-first-transition-strip.png)、[自然档](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/video-natural-transition-strip.png)、[活泼档](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/video-lively-transition-strip.png)、[回应与口型退场](../eval/avatar/dialogue-gaze-v2-preview/2026-10-14T01-28-07-016Z/video-response-strip.png)。用 FFmpeg 从视频 3 / 13.2 / 23 / 7 秒处取样，4 帧/秒、8×2 排列；这是检查取样频率，不是渲染帧率。

两次使用 Windows 无头 Chromium 149.0.7827.55，报告 AMD Radeon 780M / D3D11；各自保存 24 项来源快照与哈希及模型哈希，前后均不变，页面异常为 0。采集与拼图脚本为 [capture-dialogue-gaze-v2-preview.mjs](../scripts/capture-dialogue-gaze-v2-preview.mjs) 和 [compose-dialogue-gaze-v2-preview.py](../scripts/compose-dialogue-gaze-v2-preview.py)。

人工复查对照图及视频取样可见移视后的回看、眉眼的小幅变化及正常口型开合；选取帧中未见旧移视复活或明显虹膜漏出。三档的眉眼差异很轻，身体待机与眨眼也会造成不同画面，不能从这组自由运行截图做固定像素比较或证明自然度提升。默认仍建议自然档，活泼档供实际聊天时比较喜好。

## 当前范围

本轮沿用既有 `seed-daily-lids.vrm`：11,884,568 字节，SHA256 `16df0347c2c1b6b742f8a5b632b7a022ec65259331d95681c39f2548ddaa2eaa`。下睑仍是近似能力；L/R 使用角色侧。没有资产或服务器协议变更，没有用标点猜测语音停顿，没有用户位置追踪。

后续优先通过真实交流试听选择档位和调整频率，再在取得可信语音静音段或音素时间后尝试句内停顿移视。当前没有自然度用户研究，也没有新增 FPS、CPU/GPU 延迟或性能收益测量。
