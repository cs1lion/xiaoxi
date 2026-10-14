# 眉眼校准第二轮结果

日期：2026-10-13。[图片方法实现计划](AVATAR_CALIBRATION_PLAN.md)的六阶段技术交付已完成。本轮补齐第一轮发现的纯抬眉缺口，并增加真实播放 GPU 计时、固定轮廓测量和组合表情证据。最终角色造型与表情神态仍保留人工确认。

后续已完成[独立下睑与左右笑眼](AVATAR_CALIBRATION_V3_RESULTS.md)。下文继续保留第二轮当时的范围、指标与原始证据。

服务运行时可打开[眉眼升级实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-eyes&calibration=1#calibrationPanel)，或在聊天页选择“日常造型 · 眉眼升级”：`/?avatar=seed-daily-eyes`。原日常模型继续可选。当前候选沿用 Seed-san Daily，是技术验证资产。

正式证据：[HTML 报告](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/report.html)、[完整指标](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/metrics.json)、[独立抬眉](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/deformation-sheet.png)、[组合眨眼](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/stress-sheet.png)、[性能对照](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/performance-comparison.json)。

![实际渲染对照](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/upgrade-preview.png)

## 资产与运行接线

独立构建器从 `seed-daily.vrm` 生成 `seed-daily-eyes.vrm`，追加四个零默认权重 morph：`neuro_brow_raise_L/R`、`neuro_brow_lower_L/R`。左右完整眉毛组件各有 41 个顶点，在满权重下只沿模型局部 Y 平移 ±4.5 mm。其余顶点、原拓扑、43 个旧 morph、原始二进制前缀、材质、贴图、骨架、skin bind、标准表情、LookAt 及许可元数据均保持不变。

新验证器独立识别完整眉毛组件，检查隔离与纯平移；143 项严格检查及 9 项篡改/复现测试通过。原 `validate_seed_daily.py` 的保脸契约没有放宽。候选只增加 4,156 bytes（11,854,856 → 11,859,012 bytes），不新增 draw calls 或三角形。

| 资产 | SHA256 |
| --- | --- |
| 原 Daily | `f516bfdb7b00c87fcdbffb98c012842ca727ae835638716e704adae09271955a` |
| 眉眼候选 | `d1392e99fca810052f506bb6db6e0240c585b45fe0ee16d7258d63804352c1b5` |

适配器检测完整新通道后报告 `seed-eyes`，眉高为 `approximate:false`、`pureTranslation:true`。原 Seed/Daily 仍报告混合位置与曲率的近似眉形。新旧通道切换先清除另一模式权重，继续由 expressionManager 统一写入；眨眼、元音及 LookAt 使用既有路径。退出校准恢复原眉形模式、动画和手势过渡状态。

**左右约定明确区分。** VRM morph 和 `ParamBrowL/R` 使用角色解剖侧；ROI、额外截图场景及手动 UI 使用画面侧。正面固定画面中，画面左眉控制 `ParamBrowRY`，画面右眉控制 `ParamBrowLY`。疑惑保持原参数 0.34 / −0.04，实际抬起画面右侧眉毛；没有反转运行时原生绑定。

## 同条件图像结果

正式运行 `2026-10-13T12-32-56-681Z-15060` 保存 51 张 720×720 截图、280 项结构检查，浏览器页面错误为 0。32 个比较全部测量成功，无坐标条件拒绝；51 帧独立 humanoid head 锚点漂移均为 0 px。所有比较使用冻结坐标，不以重新平移、缩放或追踪眉毛消除误差。

眉毛测量保留全部外轮廓、极值点的所有同值样本、逐列上下轮廓和 head 相对位置。画面右上角采用预先固定的多边形排除额前头发，未随眉毛移动；两侧蒙版不采用自动最大连通区域筛选。102 个眉 ROI 均未触边，叠图复核未见发丝污染或眉毛裁切。

| 独立动作 | 重心上移 | 保守轮廓上移 | 判定 |
| --- | --- | --- | --- |
| 画面左眉 0.5 | 4.731 px | ≥4 px | 通过 |
| 画面左眉 1.0 | 9.497 px | ≥9 px | 通过 |
| 画面右眉 0.5 | 4.725 px | ≥4 px | 通过 |
| 画面右眉 1.0 | 9.461 px | ≥9 px | 通过 |

16 项变形契约全部通过：4 项移动检查、4 项对侧眉毛保持、8 项眼区保持。最大横向位移（重心与左右外缘）≤1 px，逐列上下轮廓垂直位移 spread 为 1 px，宽高变化≤1 px，阈值面积变化≤1.90%。原设阈值分别为横向≤1.5 px、spread≤2 px、宽高≤2 px、面积变化≤12%，没有为通过而放宽。顶部/底部同值点的平均 X 会随光栅化跳动，原始端点变化仍完整保存；它不被当作整条眉毛横移。

单侧抬眉时，对侧眉毛与两个金色虹膜区域的重心和面积完全不变。新形变零权重 `brow-neutral` 与原生零权重 `native-neutral` 整帧 RGB 逐像素一致。日常、开心、困困的 baseline/candidate 也一致，本轮保留第一轮调参，只在疑惑对照中切换原生/独立眉形模式。

7 张全闭截图（三种常规表情与四向压力场景）的金色阈值像素均为 0。半闭、全闭及回张图已复查；压力场景叠加疑惑眉形、四向注视、0.5/1 眨眼与 0.65 开口，未见明显虹膜残留或眼皮穿插，眉面积保持 898 / 909 px。该结论只覆盖采样场景；HSV 选中像素不是整只虹膜面积，阈值可能漏检暗部，不能证明所有连续姿态的美术正确性。

## 正常播放与 GPU 计时

实验室新增 5/10/20 秒采样面板，复用生产 Avatar RAF 和每次真实 `renderer.render`，记录 CPU wall、帧间隔、draw calls、三角形以及环境信息。异步 `EXT_disjoint_timer_query_webgl2` 查询仅在结果可用时读取；不支持、查询占用、disjoint、超时或 context loss 都明确记录，并清理查询。结束、切模型或进入校准时恢复原方法。原始 JSON 可以直接导出。

原 Daily 与候选各采样正视、自动注视、开心说话三个场景，每场景约 10 秒。两次环境均为 Windows / 无头 Chromium 149.0.7827.55 / ANGLE 报告 AMD Radeon 780M D3D11 / 720×720 / DPR 1，相机一致。说话使用本地静音口型时间线。

| 场景 | FPS 原 / 候选 | rAF P95 原 / 候选 | CPU wall P95 原 / 候选 | GPU commands P95 原 / 候选 |
| --- | --- | --- | --- | --- |
| 正视 | 59.94 / 60.07 | 16.7 / 16.7 ms | 3.3 / 2.2 ms | 1.498 / 0.891 ms |
| 自动注视 | 59.96 / 60.05 | 16.8 / 16.8 ms | 3.2 / 2.6 ms | 1.945 / 0.770 ms |
| 开心说话 | 59.97 / 60.05 | 16.7 / 16.8 ms | 3.3 / 2.5 ms | 2.042 / 0.848 ms |

原 Daily 每场景 600 个实际帧和有效 GPU query（合计 1800），候选每场景 601（合计 1803）；全部查询无 disjoint、超时或跳过。统计从原始数据重算一致。两次每帧都是 54 draw calls、73,188 triangles、36 geometries、10 textures。

这些数值描述当前采样环境，表明本次升级播放接近 60 FPS。两运行之间源码快照还含手势采样、校准恢复及实验室更新，差异已列入 `performance-comparison.json`；不是单变量资产性能实验，因此不把较低 P95 归因于新增眉毛形变。GPU 时间只覆盖 renderer 提交的命令，不包含浏览器合成或设备其它 GPU 工作；renderer 名称也不是独立硬件认证。第一轮 SwiftShader 约 10 FPS 与本轮使用不同渲染环境，不构成性能优化前后对照。

原始性能：[Daily](../eval/avatar/calibration/2026-10-13T12-28-49-812Z-36788/performance.json)、[候选](../eval/avatar/calibration/2026-10-13T12-32-56-681Z-15060/performance.json)。

## 验证、复现与边界

- `npm run test:eyes`：真实 expressionManager、新旧眉形清权与路由、眨眼和注视等眼部回归通过。
- `npm run test:calibration`：11 项；校准模式与动画状态恢复通过。
- `npm run test:performance-probe`：11 项；探针停止、异常和查询清理通过。
- 性能探针浏览器验证：默认软件路径与 D3D11 路径各 11 项。
- `npm run test:calibration:browser`：21 项；`npm run test:eyes:browser`：54 项。
- `npm run test:eyes:upgrade:browser`：18 项；含真实 morph、画面侧滑块、叠加闭眼、性能导出、提前结束、原模型恢复、移动布局和聊天入口。
- 图像分析 10 项契约测试通过；严格资产检查 143 项、资产篡改与复现测试 9 项通过。
- 最终 HTML 报告的 107 张图片全部加载，页面错误为 0；独立抬眉、组合闭眼及缩略对照已目视复查。

复跑命令见[计划](AVATAR_CALIBRATION_PLAN.md#第二轮完成状态)。分析时明确使用该运行的 `reference-manifest.json`；缩略对照可运行 `python scripts/compose-eye-upgrade-preview.py <运行目录>`。运行目录保留全部非测试 avatar runtime JS、实验室、构建/验证器、许可、模型与输入 hash、捕获参数及原生权重、浏览器环境、原始性能样本和图像。预览图使用相同裁切，不重新配准；测量继续使用原始 720px 图。

早期 V2 运行 `2026-10-13T12-25-17-338Z-17584` 曾把角色侧参数当作画面侧场景，图像证据揭示这一标注错误；修正场景/手动标签后重新正式捕获，旧运行和失败结果保留。正式捕获后只修正源清单的参考定位、开心/困困比较目的及资产 README 的坐标说明，没有修改参数、ROI、阈值或运行代码；证据包中的原始快照保持不变。实际第一轮保持参数以该次 `baselineOverrides` 为准。

独立左右笑眼、眉角/眉曲率、独立瞳孔和虹膜材质优化不在本次候选范围。当前方法和运行链已落实；人工艺术确认保持 pending，不输出角色还原百分比。资产来源与许可见 [SEED_DAILY_EYES_LICENSE.md](../web/models/SEED_DAILY_EYES_LICENSE.md)。
