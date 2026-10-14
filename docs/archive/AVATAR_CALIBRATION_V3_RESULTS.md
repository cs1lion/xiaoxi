# 独立下睑与左右笑眼结果

日期：2026-10-13。[本轮计划](AVATAR_EYELID_PLAN.md)的六阶段技术交付已完成。新候选增加左右独立下睑抬升与笑眼：开心增加下睑参与，害羞采用轻微左右差异，困困保留半睁状态；眨眼、注视、上一版独立眉高和口型继续合成。

服务运行时打开[细腻笑眼实验室](http://127.0.0.1:8000/static/avatar_lab.html?model=daily-lids&calibration=1#calibrationPanel)，或[聊天入口](http://127.0.0.1:8000/?avatar=seed-daily-lids)。实验室可分别调整画面左右下睑与笑眼、比较上一版预设、导出 PNG/JSON，退出校准恢复自然播放。普通聊天的“得意”“调侃”增加轻量下睑与笑眼；“跟随聊天表情”释放预览。

正式证据：[HTML 报告](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/report.html)、[完整指标](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/metrics.json)、[独立通道](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/deformation-sheet.png)、[组合压力集](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/stress-sheet.png)、[性能原始样本](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/performance.json)、[性能重算](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/performance-audit.json)。

![实际渲染对照](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/lids-preview.png)

## 资产与运行接线

`seed-daily-lids.vrm` 从第二轮 `seed-daily-eyes.vrm` 独立构建，追加四个零默认权重 POSITION 目标：`neuro_eye_smile_L/R`、`neuro_lower_lid_raise_L/R`。旧 Daily 与独立抬眉候选保持可选，原文件及验证规则未改。

| 资产 | Bytes | SHA256 |
| --- | --- | --- |
| 第二轮眉眼候选 | 11,859,012 | `d1392e99fca810052f506bb6db6e0240c585b45fe0ee16d7258d63804352c1b5` |
| 本轮下睑候选 | 11,884,568 | `16df0347c2c1b6b742f8a5b632b7a022ec65259331d95681c39f2548ddaa2eaa` |

独立笑眼按角色侧拆分原 `eye_smile` 的真实眼区贡献，排除 14 个嘴部顶点、3 个中线顶点、眉毛及隐藏特效。每侧包含 486 个眼区主体顶点和 137 个眼线顶点。下睑从原 `eye_close` 的局部正 Y 贡献提取，乘以 0.30、侧向增益及 0–1 mm 连续 smoothstep；每侧包含 137 个主体顶点和 29 个眼线顶点，峰值约 3.5 mm。保留原贡献中的微小 X/Z 位移，因此能力登记为 `approximate:true`、`pureTranslation:false`。本轮没有重制独立上睑。

193 项严格资产检查及 10 项篡改/复现测试通过：保护全部旧二进制前缀、旧 JSON、旧 morph、默认中性几何、拓扑、材质贴图、rig/skin、标准表情、LookAt 和许可元数据。重复位置的新增位移完全一致，接缝不匹配数为 0。原资产没有 NORMAL morph，本轮保持同样做法。资产构建记录只证明来源与几何规则，渲染边缘证据另见下文。

完整四通道才启用 `seed-lids`。校准中的 native 模式使用原双侧笑眼，并清除四个新目标；calibrated 模式分别路由左右笑眼和下睑。每个新目标乘其侧最终眼开度，全闭帧新目标归零，由既有标准 blink 输出。所有形变继续由 expressionManager 统一写入。

新参数 `ParamEyeLLower/RLower` 范围 0–1，中性为 0。参数和目标的 L/R 沿用角色解剖侧；UI、ROI 和单侧截图名称采用画面侧。正面画面左侧下睑对应 `ParamEyeRLower`，画面左侧笑眼对应 `ParamEyeRSmile`。

候选专属预设为开心 open 1.03/1.03、smile .16/.18、lower .34/.30；害羞 open .90/.92、smile .12/.16、lower .16/.22；困困仍为 open .38/.40、lower 0。聊天默认值在情绪过渡与强度缩放前混合；任一侧显式配置 smile 或 lower，包括 0，该侧就保留配置。真实生产帧测试确认高优先级手势意图不被后处理重写。

## 固定画面与像素结果

正式运行 `2026-10-13T14-55-13-321Z-15144` 保存 82 张 720×720 截图、456 项结构检查，页面错误为 0。112 个比较均可测量，坐标条件拒绝为 0；76 项技术合同通过。82 帧独立 humanoid head 锚点漂移均为 0 px。相机、灯光、模型、视口及裁切固定，未重新配准。

测量分别提取主上睫毛组件的外侧上缘，以及白色巩膜与专用金色区域并集的下缘。这是两条可见边缘，未重建完整解剖眼裂。上睫毛使用预先固定的主组件规则，明确排除小眼皮褶线并保存全部原始组件。下缘专用金色规则为 H 25–65°、S .40–1、V .25–1；原虹膜指标继续使用 S .45，未改变历史定义。固定中央带为画面左 x .35–.42、右 x .585–.665，完整逐列轮廓、中央带、蒙版和叠图均保存。

| 单侧下睑 | 中央带共同列数 | 下缘中位上移 | 上睫毛上缘最大变化 |
| --- | --- | --- | --- |
| 画面左 0.5 | 51 | 3 px | 0 px |
| 画面左 1.0 | 51 | 7 px | 0 px |
| 画面右 0.5 | 57 | 3 px | 0 px |
| 画面右 1.0 | 57 | 7 px | 0 px |

四种单侧下睑动作的全部中央带共同列下缘均向上，两侧上睫毛外侧上缘逐列保持不动。对侧眼区与眉区的 RGB 变化均为 0。8 项下睑/笑眼嘴部保护比较均为 0 RGB 变化。新旧模式中性帧整图逐像素一致。

颜色分割仍有边界局限：满强度下缘变化范围左侧 3–14 px、右侧 4–16 px。按向上位移大小统计，P10 左 4 / 右 6 px，P90 两侧 13 px；报告中的带符号屏幕 Y delta 采用相反符号与分位方向。这些极值含淡金色阈值退出造成的边界跳跃，不能全部解释为几何位移。半强度范围左 1–4 px、右 2–5 px，3/7 px 是此规则下的中央带中位数。试验中 S .30 会把全闭眼皮肤收入，H 下限 35° 会放大跳跃，两者均被拒绝；[规则审阅记录](../eval/avatar/eyelids-diagnostic/2026-10-13T14-34-19-953Z-16864/lower-rule-review.md)保留原因。

诊断运行原有 72/76 通过，四项失败是同侧笑眼后整个眉框的 RGB 不再逐像素相等。原[失败结果](../eval/avatar/eyelids-diagnostic/2026-10-13T14-34-19-953Z-16864/metrics.json)和[差异记录](../eval/avatar/eyelids-diagnostic/2026-10-13T14-34-19-953Z-16864/diagnostic-brow-rgb-differences.json)保留。正式运行仍存在 11、16、9、20 个眉框周边皮肤像素变化，单通道最大差值仅 1/255；没有把这个阈值放宽后算作整框一致。正式眉保护合同明确检查完整眉毛 mask、前后 mask 并集内 RGB、masked MAE、轮廓与重心，均要求零变化；眉毛本身全部通过。整框与眉毛内差异分别列在报告中。

17 张全闭截图的原金色虹膜阈值像素及左右下缘 mask 均为 0。空下缘 mask 明确记为轮廓不可测，没有自动判定几何正确。实际半闭、全闭、四向注视 + .65 开口，以及最大 smile + lower 的 25/50/75/100% 眨眼组合已目视复核，未见明显虹膜残留或开缝。最大组合在 blink=0 时本来就是闭合笑眼；回到 blink=0 的整图与原组合一致，称为表情恢复，不称为实际重新睁眼。

本轮无分割复核警告或 ROI 触边。原生开心/害羞与候选预设使用不同参数，呈现设计效果；只有独立形变比较与中性保护用于隔离通道语义。人工艺术确认仍为 pending，不将技术合同通过换算成角色还原分数。

## 正常播放与性能

三个场景各采样约 10 秒，使用生产 Avatar rAF 与真实 renderer.render。环境为 Windows / 无头 Chromium 149.0.7827.55 / ANGLE 报告 AMD Radeon 780M D3D11 / 720×720 / DPR 1。说话使用本地静音口型时间线。

| 场景 | 实际帧 / 有效 GPU queries | FPS | rAF P95 | CPU wall P95 | GPU commands P95 |
| --- | --- | --- | --- | --- | --- |
| 正视 | 600 / 600 | 59.95 | 16.71 ms | 3.90 ms | 1.286 ms |
| 自动注视 | 600 / 600 | 59.96 | 16.70 ms | 3.40 ms | .793 ms |
| 开心说话 | 601 / 601 | 60.04 | 16.70 ms | 3.60 ms | 1.037 ms |

合计 1,801 个实际帧与有效 GPU queries，disjoint、超时、跳过及查询错误均为 0；原始样本重算 FPS 与 P95 完全一致。每帧为 54 draw calls、73,188 triangles、36 geometries、10 textures。

此结果描述记录环境中的播放表现。GPU 计时只覆盖 render 内提交的命令，CPU wall 包含驱动、VRM 更新、提交及探针开销；renderer 名称没有独立硬件认证。第二轮与本轮运行源码和采样时机不同，不据此宣称形变新增带来性能提升。早期 .5 秒诊断样本未用作正式性能证据。

## 检查与复现

- `npm run test:eyes`：29 个 TAP 条目通过，其中已包含 14 组新增眼睑测试；覆盖真实 expressionManager、显式零值、自定义通道及真实手势层优先级。
- `npm run test:eyes:lids:browser`：36 项通过，0 异常；覆盖实际模型、单侧控制/眨眼、native 模式、真实聊天得意表情、预览释放、新旧模型切换、移动布局及 JSON 导出。[最新浏览器证据](../eval/avatar/eye-lids-ui/2026-10-13T14-44-43-193Z/)。
- 原有眼部浏览器检查 54 项、校准浏览器检查 21 项、校准单测 11 项通过。
- 图像分析 14 项契约测试通过，包含皮肤变化、眉内颜色变化及轮廓变化的区分。
- 资产严格检查 193 项、篡改/复现测试 10 项通过。
- 最终 HTML 报告的 497 张图片全部加载，页面错误为 0；独立动作、半闭/全闭与分割叠图已目视复查。[报告浏览器检查](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/report-browser-qa.json)。

```powershell
python scripts/build_seed_eye_lids.py
python scripts/validate_seed_eye_lids.py
python scripts/test_seed_eye_lids.py
npm run test:eyes
npm run test:eyes:lids:browser
npm run calibrate:eyes:v3 -- --perf-seconds 10
& 'C:/Users/18712/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' scripts/analyze-eye-calibration.py <新运行目录> --config <新运行目录>/reference-manifest.json
& 'C:/Users/18712/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' scripts/compose-eye-lids-preview.py <新运行目录>
```

证据包保留输入清单、运行源码、资产构建/验证器与许可、模型 hash、捕获参数、原生权重、浏览器环境及原始性能样本。分析使用该运行内 `reference-manifest.json`；展示图采用固定裁切，不用于替代原始像素测量。候选仍沿用 Seed-san 技术角色；独立上睑、眉角/曲率、瞳孔控制与虹膜材质优化留待后续。来源与许可见 [SEED_DAILY_LIDS_LICENSE.md](../web/models/SEED_DAILY_LIDS_LICENSE.md)。

正式捕获后只修正分析展示图沿用 V2 的“独立抬眉”标题，改为“独立眼睑与笑眼”，没有修改运行画面、配置、ROI、阈值或技术合同。原 `analysis-source.py` 快照保留；本次报告实际分析源为 `analysis-source-89771cc23ab3.py`，SHA256 `89771cc23ab3e1ad99add1f79bd4e92d44b8f5ffb9beced337ec33c2f6a454d1`，原因与两份来源 hash 记录在 [analysis-revision.json](../eval/avatar/calibration/2026-10-13T14-55-13-321Z-15144/analysis-revision.json)。
