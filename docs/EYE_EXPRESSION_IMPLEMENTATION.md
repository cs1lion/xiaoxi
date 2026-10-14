# 眼睛与表情联动

日期：2026-10-13。重点是眼睛、眉毛与表情的配合，复用现有 Three.js r180 + three-vrm 3.5.5。现已完成独立抬眉、独立下睑与左右笑眼候选，以及三轮图像校准，最新交付见[第三轮结果](AVATAR_CALIBRATION_V3_RESULTS.md)。

2026-10-14 增加倾听、思考、实际发声与打断的交流眼神时序，复用以上模型与表情。来源、实现与本轮验证见[交流眼神实现](DIALOGUE_GAZE_IMPLEMENTATION.md)。

## 直接体验

服务运行时打开：

- [细腻笑眼实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-lids)
- [交流眼神流程](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-lids&dialogue=1#dialoguePanel)
- [眉眼升级实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-eyes)
- [原日常造型实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily)
- [Seed-san 原版实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=seed)

右侧“眼睛与表情”提供日常、开心、疑惑、害羞、傲娇、困困、认真、惊讶八个预设。表情幅度以各通道的中位为起点；调到 0 时眼睛正常睁开。

在画面中移动鼠标可以观察眼睛先动、头部跟随。鼠标离开画面后回看用户。“自动环视”轮换四个关注点，“看向前方”回到居中的视线。害羞与傲娇进入时有一次短暂移视，动作结束后继续跟随当前关注点。

“眨一下”可验证半睁眼完整闭合后恢复；“静音口型演示”可验证说话与眉眼同时工作。“跟随聊天表情”渐退预览并归还现有情绪驱动；聊天入口继续使用原有七个后端情绪标签。

## 实现位置

| 文件 | 职责 |
| --- | --- |
| `web/avatar/drivers/eye-expression.js` | 八种眉眼预设、平滑切换、强度、临时移视及预览释放 |
| `web/avatar/index.js` | 合成情绪、预览、注视、眨眼和口型；提供演示 API |
| `web/avatar/render/eye-morph.js` | 按模型形变名称检测能力，注册连续 custom VRMExpression |
| `web/avatar/render/vrm.js` | 绑定 LookAt，按当前头部坐标计算眼部残余方向，提供眼部近景 |
| `web/avatar/params.js`、`drivers/emotion.js` | 参数范围及围绕中位的绝对目标缩放 |
| `web/avatar/drivers/idle.js` | 显式目标更新微眼跳锚点，自动眨眼及眼头协调 |
| `web/avatar_lab.html`、`web/avatar_lab.js` | 眼部控制与模型能力提示 |
| `web/avatar/calibration.js` | 冻结场景、原生/独立眉形比较、状态恢复 |
| `web/avatar/performance-probe.js` | 正常循环的实际帧、CPU wall 与异步 WebGL GPU timer |
| `scripts/build_seed_eye_expression.py`、`validate_seed_eye_expression.py` | 独立候选资产构建与保护契约验证 |

合成顺序为现有驱动仲裁 → 眉眼预览渐混 → 注视模式与临时移视 → 高光 → 眨眼 → 参数平滑 → VRM 输出。语音继续拥有开口与圆唇；有语音帧时保留语音嘴型。

眨眼使用 `最终开度 = 表情开度 × (1 − 眨眼进度)`，完全闭合帧保证最终开度为 0。自动眨眼跨过短暂闭合保持段时至少输出一帧完整闭合，已验证 20/30/60 FPS；原时间轴继续推进，不延长或重新触发眨眼。笑眼与睁大形变随最终开度退让，避免全闭时叠加两份眼皮形变。原生形变统一通过 expressionManager 写入，保留标准 blink、元音及 bone/expression 两种 LookAt。

修正的接线问题包括：可选 `lookAt.target` 未初始化导致赋值被跳过；眼部方向未考虑正在转动的头部；原始骨骼世界矩阵未刷新导致 LookAt 延后一帧。

## 模型能力与限制

| 能力 | Seed-san / 日常衍生 | pixiv sample |
| --- | --- | --- |
| 眨眼与注视 | 标准 blink + expression LookAt | 标准 blink + bone LookAt |
| 笑眼 | 左右联动 | 左右独立 |
| 眉高接口 | 左右独立眉形；位置与曲率混合，并非纯抬眉 | 双侧近似 |
| 独立眉角与曲率 | 无已确认的独立通道 | 双侧语义形变近似 |
| 睁大 | 原生 `eye_open` | Surprised 近似，伴随其它眼部变化 |
| 虹膜尺寸 | 原生黑眼部分大小形变 | 无确认通道 |
| 独立瞳孔尺寸 | 无确认通道 | 无确认通道 |
| 高光 | 已有高光的隐藏/显示 | 已有高光的隐藏/显示 |
| 脸红、嘴角 | 已接入 | 无确认的独立通道 |

高光滑块控制已有高光可见度，不生成环境反射或新虹膜纹理。未知 VRM 保留基础眨眼、注视和口型，并显示未适配提示。当前预设仍需要针对最终角色的眼皮和眉毛形变做美术校准。

新增 `seed-daily-eyes` 候选补齐左右独立纯抬眉/压眉，原资产不变。完整眉毛各 41 个顶点只做局部 Y 平移，满权重 ±4.5 mm；适配器优先选用该通道，也可在校准中切回原生眉形。该版尚未新增独立眉角、曲率、左右笑眼和瞳孔。参数 L/R 使用角色侧，手动滑块与 ROI 使用画面侧；画面左眉写入 `ParamBrowRY`。聊天页可通过 `/?avatar=seed-daily-eyes` 或模型选择器使用候选。

后续 `seed-daily-lids` 在上一版基础上追加左右独立下睑与笑眼，保留独立眉高。下睑为从原闭眼贡献提取的局部形变，能力登记为近似；固定截图确认中央带下缘上移、上睫毛外缘不动。开心/害羞使用候选专属值，困困维持半睁；聊天“调侃”“得意”自动参与轻量下睑。显式 smile/lower 配置，包括 0，及高层手势意图均受保护。完整眨眼时四个新目标逐侧退权。聊天入口为 `/?avatar=seed-daily-lids`，独立上睑、眉角/曲率与瞳孔尚未新增。

实验室“眉眼校准”提供固定场景、原生基线、两侧独立控制、眨眼步进、四向注视、开口、固定 ROI 及 PNG/JSON 导出。“真实播放性能”可直接测当前浏览器 5/10/20 秒 CPU/GPU 与实际帧间隔；不支持 GPU timer 时明确显示原因。

## 验证

- `npm run test:eyes`：参数中位、范围、预览切换/释放、临时移视、真实 expressionManager、头部转动方向、注视锚点与眨眼回归。
- `npm run test:eyes:browser`：加载真实模型，检查八种预设、原生笑眼/独立眉形/睁大、完整眨眼与恢复、视线方向、鼠标离开、环视目标保持、高光、强度归零、音频时钟口型及打断、日常衍生、模型切换、SVG 降级和移动布局。
- `node scripts/test-avatar-browser.mjs`：原有实验室、口型、打断、渲染器切换、加载失败保留及聊天入口回归。

以上检查已通过；眼部浏览器验证包含 54 项断言。浏览器结果和截图位于 `eval/avatar/eyes/`，对照图为 `contact-sheet.png`。验证环境为无头 Chromium + SwiftShader；不用于推断实体设备帧率。截图已复查，开心预设减少笑眼闭合幅度以保留可见虹膜。

研究背景与官方规范见 [EYE_EXPRESSION_RESEARCH.md](EYE_EXPRESSION_RESEARCH.md)。资产来源与许可沿用 `web/models/SEED_DAILY_LICENSE.md` 及原有模型说明。

后续对话配合见[交流眼神 V2](DIALOGUE_GAZE_V2_RESULTS.md)：沿用既有眉高、下睑和笑眼资产，增加错开时序、三档风格与有限移视计划。

按用户长图方法实施的固定画面校准、首轮调参、眉形方向限制与正式证据见[实现计划](AVATAR_CALIBRATION_PLAN.md)和[第一轮结果](AVATAR_CALIBRATION_RESULTS.md)。实验室新增“眉眼校准”，可对照原参数、检查眨眼中间帧并导出画面。

候选已通过独立资产验证、18 项升级浏览器检查及 16 项像素变形契约。51 帧的独立头部锚点漂移 0 px；半强度抬眉约 4.7 px、满强度约 9.5 px。D3D11 报告 AMD 780M 的正常渲染样本约 60 FPS，GPU 查询有效；全部细节及限制见[第二轮结果](AVATAR_CALIBRATION_V2_RESULTS.md)。这组性能采样与前述 SwiftShader 回归使用不同环境，不能当作优化收益。
