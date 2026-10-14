# 日常角色的拇指与基础手型

本阶段将两侧六个拇指节点接入现有手指控制，提供放松、摊掌和轻握三种完整手型。原有四指自动姿态、手臂路线、肩胸配合和语音安排继续使用；模型与蒙皮未修改。实验页的“手指自然松弛（含拇指）”是总开关，新增右手基础手型检查按钮。

## 标定依据

Daily 两手各有 15 个映射指节。拇指使用 `ThumbMetacarpal`、`ThumbProximal`、`ThumbDistal`，每节都有实际索引引用顶点的直接蒙皮权重；记录与复现代码见 `eval/avatar/hand-poses/audit.json`、`audit_model.py`。

POSITION 顶点来自逆绑定的 A 姿态，节点休息姿态为 T 姿态。审计先重建实际休息蒙皮，再固定末端皮肤顶点并计算指长轴与掌向法线。normalized 节点休息旋转为单位四元数，raw 指节局部 +Y 沿指长，两个坐标系需要转换。

右拇指的 normalized 掌向屈曲轴与三种姿态为：

| 指节 | 单位轴 XYZ | 放松角 | 摊掌角 | 轻握角 |
| --- | --- | --- | --- | --- |
| 掌骨 | .673854 / .005909 / .738841 | .08 | .025 | .16 |
| 近节 | .617273 / .012459 / .786650 | .10 | .03 | .22 |
| 末节 | .853783 / −.019185 / .520275 | .06 | .015 | .13 |

角度为弧度；左侧镜像轴 X 相同、Y/Z 取反。采样模块将轴角转成 XYZ Euler，渲染器再合成四元数。独立检查 `audit_targets.mjs` 比较生产目标和实测轴角，18 个双侧三手型节点组合通过，最大角误差约 2.98e−8 弧度。

## 手型合成

`hand-poses.js` 的 `sampleHandPose(frame, options)` 返回 30 指节偏移；`HAND_BONE_NAMES` 保留原 24 四指顺序并追加六个拇指节点，`HAND_POSE_NAMES` 为 `relaxed`、`open`、`softGrip`。仅精确 `Seed-san Daily Study` 适配。

自动四指直接复用原 `sampleFingerPose` 目标。左右拇指休息时轻弯；右拇指随招呼、否定和摊掌舒展，自我强调保持放松。展开沿原 `weight`，乘 `min(1, strength/.15) * (.65 + .35*strength)`；减少动态再乘 .4。空、未知、零包络、零强度与正常取消帧回到放松，不引入 phase 摆动。

`preview: {name, side}` 完整替换指定手目标，另一手保持自动控制，非法输入忽略。放松使用原四指休息弯曲；摊掌保留其 .2 倍微弯；轻握食指至小指三节角为 `[.28,.34,.18]`、`[.32,.40,.20]`、`[.36,.44,.22]`、`[.40,.48,.24]`。轻握是轻度蜷指，仅用于检查，尚未接入自动说话动作；检查目标在减少动态模式中保持相同，方便比较同一手型。

渲染器捕获 30 指节绑定四元数，统一合成一次 `rest * offset`，沿实际局部旋转以 `1-exp(-18*safeDelta(dt))` 平滑接近目标。零帧时间和切换保留当前姿态；关闭后全部指节平滑回到绑定。自动控制跳过缺失指节，完整手型检查要求指定侧 15 节全部可用。

## 检查与停止

`setHandPosePreview(name = null, side = 'right')` 选择或清除检查姿态；非法名字/侧别抛出 `RangeError`，关闭控制或缺失完整指定手时返回 `false`。`getHandPosePreview()` 返回 `{available, enabled, name, side}`。实验页启动现有 2.4 秒摊掌预览再选择右手手型，结束后自动收回，也可切换或退出。

检查状态只保留在实验页的摊掌预览帧中。语音、其他手势、完成、倾听/思考和打断清除检查选择，恢复由渲染时间推进。关闭总开关、释放或切换渲染器同样清除。按钮在 `AudioContext.resume()` 后校验停止序号、当前渲染器身份、加载/校准/控制状态，避免已取消的异步请求重新启动。

眼睛校准保存检查字段与实际指节变换，固定画面将全部已捕获指节恢复绑定后以零帧时间绘制；退出恢复进入前实际姿态与检查选择。

## 验证

综合 68 项单元检查通过，覆盖轴角转换、镜像、原四指兼容、三手型、有限数值、强度连续性、缺失节点、完整手要求、实际四元数切换、开关/停止恢复、校准恢复和既有手臂/肩胸路线。最终完整手要求与校准的受影响 18 项再次通过。

```powershell
node --experimental-default-type=module --test web/avatar/test_hand_poses.js web/avatar/test_hand_rig.js web/avatar/test_finger_poses.js web/avatar/test_finger_rig.js web/avatar/test_body_poses.js web/avatar/test_body_rig.js web/avatar/test_calibration.js web/avatar/test_gesture_paths.js
```

真实模型的前后生产源码、模型指纹、固定近景/侧视、动态序列与浏览器报告保存于 `eval/avatar/hand-poses/`。实际蒙皮画面用于确认姿态；骨骼角度和最近顶点距离是辅助观察。可见间距不代表接触求解，原有低模指节折线与腕部/袖口硬感仍在。

正式手型浏览器检查通过 6 组，随后顺序通过既有动作衔接 7 组和语音表演 9 组，共 22 组。浏览器错误、麦克风访问、真实语音/模型服务调用及 API 写入均为零。最终报告为 `after/report.json`、`eval/avatar/pose-transitions/results.json`、`eval/avatar/speech-performance/results.json`；七个生产源码指纹与未变的模型 SHA256 已同当前文件核对。

捕获覆盖 14 个自动固定姿态和左右各三种手型，固定主镜头/全身镜头与基于手掌骨位置的近景；20 种固定模式的正/侧近景均确认实际引用的全手顶点在画面内。动态按 60 Hz 运行生产 draw 和骨架更新，按 20 fps 实际捕获图像：自我强调→摊掌→收回 289 个采样点、招呼→打断 181 个、三手型→自动 241 个，共 711 个控制采样点。自动四指最大实测四元数差约 5.96e−8 弧度，局部手臂、肩胸及头部行为保持；本方法不构成设备帧率结论。

实际零帧时间切换、30 指节关闭/恢复、原生音频时钟冻结后的打断恢复及等待 resume→停止→恢复完成后的取消均通过。独立视觉复核审查 14 张正式静态图、三型拼图、休息/招呼/摊掌前后对照及 8 个真实动态帧，未见抽样可见的新指掌插入或倒折；验收范围保存于 `visual-review.json`。稀疏掌部顶点集合不能作为掌面表面间距或碰撞证明。

展示材料为 `hand-pose-sequence.gif`（放松→摊掌→轻握→自动）、`basic-hand-poses.png`（相同手臂和侧面镜头的三型）、`rest-thumb-comparison.png`、`wave-thumb-comparison.png`、`palm-thumb-comparison.png`。这些图均来自实际模型渲染；合成脚本为 `create-review.py`。
