# 图片指导方法的实现计划

日期：2026-10-13。依据用户提供的《让 Agent 把角色画对》长图，承接 `AVATAR_VISUAL_CALIBRATION_ASSESSMENT.md`。以现有 Seed-san Daily 技术模型实施方法，保留模型身份、脸部网格、骨架、材质和已有表情绑定。六阶段的技术交付已完成，包含[第一轮校准](AVATAR_CALIBRATION_RESULTS.md)和[第二轮独立抬眉与 GPU 证据](AVATAR_CALIBRATION_V2_RESULTS.md)。最终角色身份及表情神态仍待人工确认。

## 本轮交付与顺序

| 阶段 | 实现 | 验收 |
| --- | --- | --- |
| 1. 参考与范围 | `config/avatar-eye-calibration.json` 登记方法参考、模型身份、修改前基线、待人工确认的表情意图、ROI 和保护项 | 每个输入都有明确用途；没有把示例角色或历史拟合指标写成本项目目标 |
| 2. 固定场景 | 开发用 `AvatarCalibration` 会话及实验室控制：固定视口、相机、灯光、头身姿态、注视与眨眼相位 | 重复采样一致；镜头/模型改变时旧会话失效；退出恢复自然动画 |
| 3. 表情校准 | 对开心、困困、疑惑分别提出一个假设，限定允许改动的通道；保留原参数 | 同一场景生成 baseline/candidate；说明预期变化和观察结果；保留人工判断 |
| 4. 图像与动态证据 | 捕获全部预设、25/50/75/100% 眨眼、恢复、四向注视与笑眼说话；ROI 蒙版、面积/轮廓/色差、对照页 | 指标写明坐标、单位、阈值与范围；不合并成还原百分比；闭眼泄漏检查来自实际截图 |
| 5. 真实播放基线 | 正常 Avatar 动画与渲染下采集完整 rAF 帧间隔、P95、主线程更新时间、draw calls/triangles | 不跳过中间渲染；CPU 耗时与 GPU 耗时分开；记录浏览器/设备环境 |
| 6. 可复现交付 | 独立时间戳目录、模型与输入文件 hash、参数、相机、组件关系、图像、JSON 与 HTML | 新运行不覆盖历史；命令可复跑；报告可直接打开 |

## 第一轮假设

- **开心**：上睑闭合与笑眼共同压缩虹膜，易接近困眼。允许只改眼开度和笑眼幅度；保护眉毛、嘴角、脸红和虹膜意图。
- **困困**：持续半睁眼与开心区分不足。允许只改左右开度；完整眨眼后恢复同一目标。
- **疑惑**：挑眉高度差不够显眼。允许只改左右眉高；保持眼形、嘴型和注视。
- **日常**：作为固定场景基线保留现有参数。

上述意图是本轮试调设计，尚非用户确认的最终角色表情。参数和颜色阈值对应 Daily 技术模型；人工检查仍负责角色神态和自然程度。第一轮保留开心与困困调参；疑惑增加眉高的假设被实际图像和顶点数据否定，已撤回至原值，专门抬眉形变列为后续资产工作。

## 第一轮完成状态

参考清单、固定场景模块、实验室校准面板、参数基线、图像测量、真实播放采样和证据目录均已完成。会话在模型、相机、视口或外观变化后失效；退出恢复自然动画，窗口缩放后按新尺寸恢复镜头。

校准面板采用方形画面，使本轮手工眼区可随尺寸等比使用。日常模型开放原参数与区域框，其它 VRM 只开放固定姿态检查。可组合表情幅度、25% 步进眨眼、四向注视和开口，并导出本帧 PNG 与参数 JSON。

32 张正式截图通过 185 项结构检查；独立头部锚点最大漂移 0 px。校准面板 21 项浏览器检查、眼部 54 项浏览器检查、11 项校准单测、图像分析 3 项契约测试以及原有 Avatar 浏览器回归通过。指标和审美判断保持分离，疑惑的纯抬眉目标尚未实现。

## 复跑与查看

现有服务运行在 `http://127.0.0.1:8000` 时：

```powershell
npm run test:calibration
npm run test:calibration:browser
npm run calibrate:eyes
& 'C:/Users/18712/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' scripts/analyze-eye-calibration.py <新生成的运行目录>
```

`npm run calibrate:eyes` 同步清单后生成新的时间戳目录，每个正常播放场景默认测量 5 秒。分析脚本需要 Pillow 与 NumPy，上述命令使用本机附带的 Python。其它环境可用安装了这两个库的 Python。报告、蒙版及对照图保存在同一运行目录。

修改 ROI、阈值或参考用途时先编辑 `config/avatar-eye-calibration.json`，再运行 `node scripts/sync-eye-calibration.mjs`；浏览器读取的副本为 `web/avatar/eye-calibration.json`。

## 指标如何使用

同一场景采用恒等配准，固定场景 fingerprint 校验相机、投影、视口和模型身份。眼表情变化不参与重新配准；头部独立锚点用于检查场景是否漂移。发生场景变化时拒绝前后比较，重新生成一组基线。

眼区内的颜色阈值只是 Daily 金色虹膜的半自动可见区域提取。报告分别给出可见面积、重心、宽高比、蒙版 IoU、平均/P95 边缘距离和 ROI 色差。修改前基线用于说明改了什么；它不等于审美真值。未提供独立目标标注时，不产生“接近原图”的准确率。

实际模型组件登记按已有 morph、骨架和材质建立，明确左右联动、近似通道和缺失项。SVG 的 clipPath/pivot 不能直接替代 VRM 的形变和深度关系。

## 第二轮完成状态

第一轮发现的纯抬眉缺口已在独立候选 `seed-daily-eyes.vrm` 中补齐：新增左右抬眉、压眉四个零默认权重形变，只沿局部 Y 移动完整眉毛组件。原 Daily 资产保持可选；原换衣保脸验证没有放宽，新候选由独立构建器与严格验证器检查。

实验室及聊天入口可选择“眉眼升级”。校准可在同一候选资产和固定坐标中比较原生眉形与新形变，手动控制画面左右眉毛，检查眨眼、四向注视与开口叠加。参数 L/R 沿用角色解剖侧；区域框、截图场景和手动标签采用画面侧，画面左眉对应 `ParamBrowRY`。

真实播放采样面板复用生产动画循环，可在当前浏览器记录 5/10/20 秒的帧间隔、CPU wall、实际 WebGL GPU 命令耗时和环境信息，并导出原始 JSON。无 GPU timer 支持、查询冲突或 disjoint 时记录原因和丢弃计数，不以 CPU 时间代替 GPU 时间。

正式第二轮捕获包含 51 张截图、280 项结构检查，以及独立抬眉、对侧保持、眼区保持的 16 项变形契约。完整外轮廓、极值端点、逐列上下轮廓、固定排除区、原始参数和 head 锚点都保存在证据包中；不会把技术通过状态写成艺术确认。

第二轮复跑：

```powershell
python scripts/build_seed_eye_expression.py
python scripts/validate_seed_eye_expression.py
python scripts/test_seed_eye_expression.py
npm run test:eyes:upgrade:browser
npm run test:performance-probe
npm run test:performance-probe:browser
npm run calibrate:eyes:v2 -- --perf-seconds 10
& 'C:/Users/18712/.cache/codex-runtimes/codex-primary-runtime/dependencies/python/python.exe' scripts/analyze-eye-calibration.py <新运行目录> --config <新运行目录>/reference-manifest.json
```

V2 清单为 `config/avatar-eye-calibration-v2.json`，同步副本为 `web/avatar/eye-calibration-v2.json`。分析历史运行时明确传入该次捕获的清单副本，避免后来参数或 ROI 修改影响历史结论。Windows 的默认正式采样请求 ANGLE D3D11，实际使用的 renderer 和 GPU 支持以输出环境为准；`--gpu-mode software` 可显式复跑软件渲染。

## 超出本计划的后续资产方向

独立下睑与左右笑眼的后续候选已完成，执行依据见[眼睑计划](AVATAR_EYELID_PLAN.md)，资产保护、实际渲染、浏览器回归及性能证据见[第三轮结果](AVATAR_CALIBRATION_V3_RESULTS.md)。原第二轮证据保持不变。

若目标只能通过独立眉角、眉曲率、上下睑或左右笑眼表达，另建候选模型版本。为表情升级新增验证范围，继续保护原拓扑、骨架、已有眨眼、LookAt 与元音绑定；原 `validate_seed_daily.py` 的换衣保脸契约保持有效。

虹膜材质优化在表情目标清楚后单独进行。性能优化依据本机测量选择 VRM 更新、透明材质或像素负载方向。二维缓存、参考案例的拟合残差和 Mac FPS 数字不直接移植为验收值。
