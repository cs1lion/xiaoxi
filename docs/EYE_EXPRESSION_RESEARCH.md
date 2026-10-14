# 眼睛与表情配合：实现研究

日期：2026-10-13。依据用户截图，以及后续明确的重点“眼睛相关的调动配合表情”。

第一版已实现，当前行为、演示入口与验证见 [EYE_EXPRESSION_IMPLEMENTATION.md](EYE_EXPRESSION_IMPLEMENTATION.md)。本文保留实施前的研究观察；“当前项目的真实缺口”描述当时状态。

## 推荐方案

沿用现有 Three.js + three-vrm，增加独立的眉眼通道适配和最终合成器。把表情写成一组眼睑、眼型、眉毛与视线行为的目标，再与眨眼、鼠标注视和说话合成。

关键是让同一个“开心”同时改变下眼睑、笑眼曲线和眉形，并保留注视能力；“困困”能保持半睁眼，同时完整眨眼。增加表情按钮本身解决不了这个问题。

截图里的帽子与发束有多边形明暗，具备 3D 观感，但单帧不足以确认实际使用 VRM、GLB、MMD 或 Live2D。已搜索“眼内反光”“眼睑与眉嘴配合”“在画面里移动鼠标，让它看向你”等文字，未找到可信的原始页面。部分搜索服务超时或要求验证，不能把检索受限当作原页面不存在的证据。下文的实现依据来自官方规范、当前运行时源码及本地实验。

## 控制通道

| 通道 | 负责什么 | 与表情的关系 |
| --- | --- | --- |
| 注视 X/Y | 虹膜或眼球朝向 | 日常、开心、疑惑保留当前关注点；害羞可短暂移开再回来 |
| 左右眼睑开度 | 睁大、半睁、闭眼 | 与眨眼合成；左右可轻微不同 |
| 眼型 | 下睑抬升、笑眼、眯眼、惊讶轮廓 | 需要模型实际提供相应形变，不能只缩放整只眼睛 |
| 左右眉高、角度、形状 | 挑眉、压眉、内眉抬起 | 配合眼型建立疑惑、认真、得意等区别 |
| 虹膜/瞳孔尺寸 | 风格化的注意和惊讶变化 | 尺寸是两个不同的美术通道；是否能分别控制取决于资产 |
| 高光可见度 | 已有高光的明暗/隐藏 | 可作表情辅助，保持独立于虹膜主色 |
| 嘴角与脸红 | 补充表情可读性 | 微笑不独占说话开口，害羞可配少量脸红 |

Live2D 官方标准参数将 EyeOpen、EyeSmile、EyeBallX/Y、EyeBallForm、左右眉形分别列出。它提供了可借鉴的拆分方法，不代表使用相同参数名后模型会自动产生这些形变。

## 表情编排起点

截图可见的六种表情如下；其余两种没有出现在截图范围内。下面的幅度和行为是实现建议，需要看最终模型校准。

| 表情 | 眼睛 | 眉毛与视线 | 嘴/脸补充 |
| --- | --- | --- | --- |
| 日常 | 放松睁眼，保留轻微眼跳和眨眼 | 稳定注视 | 接近中性 |
| 开心 | 下睑略抬，笑眼增加；避免长时间完全闭眼 | 眉形舒展，仍看向用户 | 小幅上扬 |
| 疑惑 | 两眼开度略有差异，避免变成瞪眼 | 单侧挑眉，注视保留；可轻微侧头 | 小幅不对称 |
| 害羞 | 开度略收，避免持续闭眼 | 短暂侧下看，再回看；眉内侧略抬 | 少量脸红、含蓄笑 |
| 傲娇 | 上睑略压，带一点侧目 | 一侧眉压低，避免整脸切成愤怒 | 轻微偏嘴角 |
| 困困 | 半睁，睁眼阶段放慢 | 眉毛放松，注视变化幅度缩小 | 放松闭口 |

如需八种预设，可另加“认真”和“惊讶”：前者以稳定注视、轻压眉和收敛笑意为主；后者以抬上睑、抬眉和短暂保持为主。这两种是建议项。

变化应有短的进入、保持、退出过程。例如害羞的移视只占一段动作，随后回到当前关注点。不要让所有非日常表情都永久抢走鼠标视线。

可从眼神约 60–120 ms、眼型约 180–300 ms、眉形约 200–400 ms 的过渡起步，头部略晚跟随。这些是美术试调范围，不是已验证的人体运动常数。自动眨眼继续保留自己的快速闭合、短暂闭眼、较慢张开阶段。

## 眨眼怎样与表情共存

假设表情睁眼度为 `o`，眨眼闭合进度为 `b`，二者都在 0–1：

```text
最终睁眼度 = o × (1 − b)
最终闭合量 = (1 − o) + o × b
```

例如困眼开度 0.4，眨眼会在 0.4 → 0 → 0.4 之间变化。直接把开度固定覆盖为 0.4 会阻止眨眼；把多个闭眼 morph 满权重相加又可能挤坏眼皮。

Live2D 官方表情文档直接比较了眼睛开合值 0.4 的 Add、Multiply、Overwrite，指出 Multiply 保留眨眼波形，Overwrite 会忽略眨眼。上述公式是应用层建议，VRM 的 override 设置不会自动实现这个公式。

3D 中眼睑和笑眼可能是不同 morph。建议统一决定最终闭合量，并在眨眼趋近完全闭合时让冲突的笑眼/眯眼形变退让。能否线性混合取决于模型形变设计，需实测中间值；不能仅凭参数名称假定安全。

## 当前项目的真实缺口

### 注视目标接线被可选字段判断挡住

`web/avatar/render/vrm.js:144` 仅在 `'target' in vrm.lookAt` 为 true 时赋值目标。当前 three-vrm 3.5.5 构造器不初始化可选的 target 字段，因此该判断初始为 false。

浏览器实测原始页面 targetBound=false，将 ParamEyeBallX 从 0 改成 1 后 yaw 仍为 0；在隔离页面直接赋值目标后，同一输入的 LookAt yaw 约为 18.17°。这是目标计算角度，实际眼骨角度还会经过模型 rangeMap 缩放。生产修正应检查 lookAt 对象是否存在后直接赋值：

```js
if (vrm.lookAt) {
  vrm.lookAt.target = state.target;
  vrm.lookAt.autoUpdate = true;
}
```

现有“鼠标参数已经改变”不等于实际眼球已转动，应验收到模型输出。

### 参数有了，但 VRM 输出不完整

`web/avatar/params.js` 已定义眼睑、笑眼、注视、虹膜、瞳孔和左右眉参数。`web/avatar/render/vrm-mapping.js` 主要消费开度与注视，眉毛被粗略压成 sad/angry；EyeSmile、EyeBallForm、Pupil 尚未接到 VRM。

现有高层情绪只映射 happy/sad/angry，并过滤 binary 或带有 blink/mouth/lookAt 覆盖的表情。Seed-san 的这三个表情都是 binary，因此被跳过；sample 的 happy/sad 也因覆盖配置被跳过。这项保护有合理目的，不能简单移除过滤再让整脸预设与眨眼、口型争用。

### 眼睑目标的缩放语义需要修正

`web/avatar/drivers/emotion.js` 的 `_scale()` 把绝对目标乘强度 gain，但正常睁眼的中位是 1。当前 base=0.97、arousal=0.25 时，gain=0.5875，目标被缩成 0.569875，意外接近半睁。

正确的绝对目标缩放起点应为：

```text
scaled = neutral + (target − neutral) × gain
```

该例应约为 0.982375。中位是 0 的眉形等通道仍可直接乘 gain。表情之间的混合也应按每个通道的中位处理缺省值。

### 参数范围会截断瞳孔变化

`web/avatar/index.js` 写入 `ParamPupil = 1 + arousal × 0.45`，但 `Rigger.commit()` 把全部参数统一限制在 −1..1，因此超过 1 的部分被截掉。应声明各参数范围，或用归一化“变化量”再在渲染端换算尺寸。即使修正范围，VRM 仍需实际提供可用瞳孔形变。

### 现有眨眼并没有被情绪层直接覆盖

Rigger 当前采用偏移相加，只有显式 mute 才独占参数；自动眨眼以开度扣减进入。因此不能把所有问题归因于层级高低。应分别检查绝对目标语义、混合曲线和最后写入模型的形变。

## 可复用的模型形变

下面按原生名称识别，不应将数字索引写死到通用适配器里。

| 能力 | Seed-san | pixiv sample |
| --- | --- | --- |
| 左右眨眼 | blink_L、blink_R | Fcl_EYE_Close_L/R |
| 笑眼 | eye_smile | Fcl_EYE_Joy_L/R、Fcl_EYE_Fun |
| 睁大/收窄候选 | eye_open、eye_small、eye_zitome | Fcl_EYE_Surprised、Angry、Sorrow |
| 眉形 | eye_brow_up/down 及左右通道 | Fcl_BRW_Angry/Fun/Joy/Sorrow/Surprised |
| 黑眼部分大小候选 | eye_black_big/small | 暂无确认的独立瞳孔通道 |
| 高光隐藏 | eye_no_hilight | Fcl_EYE_HighlightHide |
| 脸红候选 | cheek | 未确认 |

候选名称只证明通道存在，不证明它对应精确的生理结构或最终视觉含义。sample 的 Surprised 实验也会改变虹膜观感，不能把它假定为纯上眼睑通道。

Seed-san 的 lookAt 类型为 expression，方向由 look_up/down/left/right 驱动；sample 为 bone。适配器需要保留各自的注视控制权，不能每帧清空全部 morph。

## 合成与写入顺序

建议增加 `EyeExpressionMixer` 和按模型配置的 `EyeMorphAdapter`：

1. 读取当前情绪，产出左右眼型、开度和眉形目标；仅有明确移视动作的预设临时接管 gaze。
2. 合成眨眼、注视以及必要的上下视线与眼睑配合；分配冲突形变的权重。
3. 通过模型能力配置按名称解析形变，缓存 mesh/index；缺失能力明确降级。
4. 优先注册独立、连续的 custom VRMExpression，由同一个 mixer 写权重，设置与各通道责任一致的 override。
5. 调用 `vrm.update(dt)` 后渲染；每个共享形变只保留一个最终写入者。

three-vrm 3.5.5 中，expressionManager 会先清零其绑定的 morph，再逐个加总表情。提前直接写同一个 `morphTargetInfluences` 会被清掉；事后独立再写则可能覆盖或重复叠加。

override 的真实含义：

- `none` 保留程序化动作。对不改变眼睑的眉形、高光通道很合适；对闭眼眼型仍需 mixer 处理重复闭合。
- `blend` 随表情权重降低程序化动作，具体为 `max(0, 1 − overrideAmount 合计)`。
- `block` 在表情有输出权重时阻断相应动作，连续渐入时可能表现为突然停眨眼。
- `overrideLookAt` 在 manager 中主要抑制四个方向 expression；bone 类型的眼骨旋转需应用层另行处理。

## 高光作为辅助通道

本地两个 VRM 都有高光隐藏 morph，可以先实现“已有高光的可见程度”。它不等于动态环境反射，也不会自动生成截图中的复杂虹膜纹理。

如果要做可调美术反光，可以独立眼材质使用 MToon MatCap，通过 `matcapFactor` 控制强度。MatCap 使用视空间法线采样；VRM1 的 materialColorBinds.matcapColor 对应此系数。主虹膜纹理里烘焙的白点不能由通用参数单独关掉，需要拆分贴图/高光层。纹理变换绑定也不影响 MatCap。

## 已做的本地实验与下一步验收

已在无头浏览器加载实际 pixiv sample，停止动画，在隔离页面中补接 LookAt 目标，分别调用原生视线、笑眼+眉形、惊讶眼型+眉形、收窄眼型+眉形和高光隐藏。六张截图及调用记录保存在：

- `eval/research/eye-expression-reference/contact-sheet.png`
- `eval/research/eye-expression-reference/observations.json`

实验没有改动产品代码和模型文件。观察到眼、眉和高光可以分别改变，浏览器没有 pageerror。为了隔离变量，实验在 vrm.update 后手工写原生 morph 并再次渲染；这是静态研究方式，不是推荐的生产更新顺序。

它证明现有资产有可用通道，尚未证明完整连续动画、组合眨眼、语音叠加或最终美术质量。对照图只展示眼部，使用的是官方示例角色。

实施时先补接注视目标、修正目标语义和参数范围，再接独立眼部通道，最后编排表情。验收应重点覆盖：

- 相同嘴型下，仅看眉眼也能分辨开心、疑惑、害羞、傲娇和困困。
- 半睁/笑眼状态能完整闭眼，再回到原表情；无眼皮穿插、虹膜露出异常。
- 表情切换时保留鼠标注视，明确移视动作结束后恢复关注点。
- 不同注视方向下，睫毛、上下眼睑、高光和虹膜仍协调。
- 说话只控制语音开口，不把眼睛重新归零；打断后表情平滑保持或退出。

## 资料

1. [Live2D 标准参数表](https://docs.live2d.com/en/cubism-editor-manual/standard-parametor-list/)：眼睑、笑眼、注视与眉形的独立通道。
2. [Live2D 表情混合](https://docs.live2d.com/en/cubism-sdk-manual/expression/)：眼睛开合 0.4 时 Add/Multiply/Overwrite 与眨眼的差异。
3. [Live2D 自动眨眼](https://docs.live2d.com/en/cubism-sdk-manual/autoeyeblink/)：眨眼参数组及各阶段。
4. [VRM 1.0 表情规范](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_vrm-1.0/expressions.md)：形变、材质和程序化动作 override。
5. [three-vrm 3.5.5 ExpressionManager 源码](https://github.com/pixiv/three-vrm/blob/ff42fae4fcee1fcbca2cd262c7f5f8cbddeaf5ab/packages/three-vrm-core/src/expressions/VRMExpressionManager.ts)：清零、加总与 override 计算。
6. [MToon 1.0 规范](https://github.com/vrm-c/vrm-specification/blob/master/specification/VRMC_materials_mtoon-1.0/README.md)：MatCap 的采样、强度和纹理变换限制。

官方资料用于实现原理核对，并非截图原站技术栈的证明。
