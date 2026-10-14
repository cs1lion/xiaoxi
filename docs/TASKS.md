# TASKS — Phase 2 任务看板

**维护人：Role 0（唯一可写）。** 各 Role 在汇报里报告状态变更，由 Role 0 更新本文件；执行 Agent 不得自行改写，避免并行写冲突。
**基线：** `v0.5-phase1-freeze`（commit `a6620c5`）｜契约更新：`16fceba`
**当前状态：** 2026-10-14 全部完成并通过集成验收（见文末验证记录）。
**图例：** `[x]` 完成且测试全绿 ｜ `[~]` 进行中 ｜ `[ ]` 待办 ｜ `[!]` 阻塞（等 `[PROTOCOL_REQUEST]`）

## 流程与规范（Role 0）

- [x] 阶段一封盘：健康检查、DB 备份、PARKED_TASKS 盘点、21 份实验报告归档
- [x] `AGENT_PLAYBOOK.md`：角色白名单 + 四条铁律
- [x] `PROTOCOL.md` 第十至十三节：F1–F4 契约
- [x] `check_protocol.py` 第五类检查：REST 接缝（前端引用的 /api 路由必须存在）
- [x] `TASKS.md` / `WORKFLOWS.md` / `TESTING.md` 三件套 + 三个协作仪式
- [x] 打标 `v0.5-phase1-freeze`

## F1 记忆内化与日记融合（Role 1）

- [x] 开工前协议红线自检（结论：契约够用；spent 不注入/heat∈[0,1]/日记标注用 persona name 已写回协议）
- [x~] 按天日记合成与程序化校验（diary 表 + 向后兼容迁移）
- [x ] drift / spark 状态机与上限控制
- [x ] `persona.py` 的 `bias_block()` 纯函数 + 单测
- [x ] `diary_notice` 事件接线（`[PROTOCOL_REQUEST]` → Role 0）
- [x ] `GET /api/memory/diary` 路由（router 工厂 + Role 0 合入 app.py）
- [x ] 里程碑打标 `v0.6-phase2-diary`

## F2 Neuro 式长语音（Role 2）

- [x] 开工前协议红线自检（结论：契约够用；app.js/index.js 透传送集成阶段）
- [x~] orchestrator 微句切分 + `audio.chunk` 字段 + 可选 `speech_plan`
- [x ] 预加载合成队列（并发有界、失败跳过、index/seq 单调）
- [x ] 双工打断：`barge_in` 后取消合成、`interrupted` 后零迟到 audio
- [x ] `tts.py` 长文本入口（旧签名保持兼容）
- [x ] `timeline.js` 呼吸停顿与微表情（字段缺失时等价现状）
- [x ] `config/pacing.yaml` speech 段（默认关闭新特性时等价现状）
- [x ] 里程碑打标 `v0.6-phase2-speech-chunking`

## F3 后台自进化（Role 1）

- [x ] `assistant/loop.py` 自治模式（有界复盘 + trace）
- [x ] `self_state` 持久化与注入路径（禁 subject="user"）
- [x ] `self_evolution` 事件 + `source:"self_topic"` 主动开口接线
- [x ] `config/memory.yaml` `self_evolution` 段（默认全关）
- [x ] 里程碑打标 `v0.6-phase2-self-evolution`

## F4 星图记忆（Role 1 底层 + Role 3 前端）

- [x ] Role 1：starmap 图谱查询 + `node/{id}` 详情 + 路由工厂
- [x~] Role 3：星图数据桥接与渲染（漫游/检索/详情/narrate 状态机）
- [x ] Role 3：主工作台视图接入 diff（`[PROTOCOL_REQUEST]` → Role 0 合入）
- [x] Integration Check：Role 1 ↔ Role 3 请求/响应对齐（字段逐项核对 + live 冒烟通过）
- [x ] 里程碑打标 `v0.6-phase2-starmap`

## 集成与封版（Role 0）

- [x ] Integration Check：Role 1 ↔ Role 2（`bias_block` 调用点）
- [x~] Integration Check：Role 2 ↔ Role 3（`audio.chunk` 字段透传：app.js:250 归 Role 0，index.js:317 已转交 Role 3 并入交付）
- [x ] 汇总 `[PROTOCOL_REQUEST]`，统一接线 app.py / app.js / index.html
- [x ] 接线后重跑全局门禁 + 受影响浏览器回归
- [x ] 总封版打标 `v0.6-phase2`


## 验证记录（2026-10-14，Role 0）

- 全量 Python 回归：`scripts/test_*.py` 全跑，仅 7 个既有失败（缺 PIL 的 eye_calibration_analysis；依赖已清理 /eval 数据的 memory_eval 与 5 个 procedural_memory_*）——与 `v0.5-phase1-freeze` 基线逐项对照确认同为既有失败，非本次引入。
- 全量浏览器回归：`scripts/test-*.mjs` 共 10 个失败，基线上同样全部失败（WebGL/d3d11 环境限制），本次合入零新增失败。
- Live 冒烟（`eval/smoke_phase2.py`，gitignored）：starmap/日记 REST、页面挂载、`/ws` narrate 全链路（`turn_started→thinking→emotion→text_delta→done`）通过；冒烟写入的测试记忆已清理。
- 集成回归修复记录：`test_voice_turns` 4 例 proactive 红（`_maybe_self_evolution` 未进编译白名单）与 `test_thread_materials_api` 红（loop.py 模块级导入触发建库）均已修复并复跑转绿。

---

# 打磨轮 · 2026-10-14 批准（既有功能精修，基线 v0.6-phase2）

> 四项强制修正已并入：① 全链路首字提速（speech/self_evolution 默认 true + 首句激进切分 + 思考期预演 + deliver_reply 微句化）；② 默认开启灵动感知（搭话心跳 + 视线跟随默认开 + 区域化点击互动）；③ 默认推荐 seed-daily-hair 造型 + 舞台打光；④ 界面极简（禁平铺新按钮 + 胶囊输入框 + 低频控件入设置面板 + 副玩法统一内嵌）。

## Role 0（基线清零与主舞台整合）

- [x] R0-1 测试清零：Python 7 个（PIL 守卫 + 六个夹具 setUpModule 优雅 skip）、浏览器 10 个（7 个夹具 skip 守卫、companion/note-proposals 补 dialogue-review.css 白名单、task-card 补 work-tasks Tab 点击）——全量回归零失败
- [x] R0-2 主动搭话心跳：app.js 45s 心跳 + 本地空闲门控 + onclose 清理；proactiveToggle/followToggle 默认开；协议第六节补心跳契约
- [x] R0-3 孤立页归档：blender_study / avatar_preview 移入 web/archive（含 README 索引）；card_preview / notepad_preview 有测试背书保留
- [x] R0-4 配置翻转：pacing.speech.enabled=true、memory.self_evolution.enabled=true、主动间隔调敏（idle 900s / min_interval 1800s / 复盘 60min）
- [x] R0-5 默认推荐造型接入：seed-daily-hair 选项 + URL 映射 + 启动默认（失败回退 SVG）

## Role 2 / Avatar 专项（微句首包提速 + 呼吸落地 + 叠加防抽搐 + 触控热区 + VRM 美化）

- [ ] A-1 首句激进切分（6~12 字遇标点即切）+ 后续恢复 12-35 字；段间停顿由 playback 消费（pause_after_ms 定停顿、停顿期口型放松、breath 点轻眨眼）
- [ ] A-1b deliver_reply 微句化：narrate/self_topic/助手结果同样获得 chunk 字段与呼吸停顿
- [ ] A-2 思考期预演：thinking→首包音频间播放思考微表情（轻眨眼+视线微移+轻微歪头）
- [ ] A-3 idle 层多驱动叠加软限幅（sway/Motion Magic/beat/dialogueExpression），消除过冲抽搐；before/after 截图
- [ ] A-4 触控与区域互动：Pointer Events 统一鼠标/触摸；摸头（眯眼笑）/戳脸（惊讶微嗔）差异化反馈 + 情绪氛围背景光
- [ ] A-5 VRM 美化：seed-daily-hair 舞台镜头构图 + 柔和边缘光/呼吸景深固化；SVG 备选造型发丝次级摆动与腮红高光改善；外部 .vrm 一键加载入口（与 Role 3 的设置面板控件对接）

## Role 3（主界面大瘦身 + 胶囊输入框 + 副玩法统一内嵌）

- [ ] U-1 底部输入区重构为现代悬浮胶囊（Capsule Bar）：仅保留【语音通话/麦克风】【文本输入】【工作台抽屉开关】，移除杂杂乱次级按钮
- [ ] U-2 低频/调试控件收纳进右上角【⚙️ 设置面板】：形象切换（含 Role 2 交付的造型清单与外部 .vrm 加载入口）、视线开关、语气试音台（voice_taste 收编）、上下文预算等
- [ ] U-3 副玩法统一内嵌：一起写/共同事项/星图/音乐/实时字幕/英语练习整合为工作台抽屉内的统一导航视图；music/interpret 不再整页跳转离开角色
- [ ] U-4 practice 入口从 details 深处提为一级控件
- [ ] U-5 模式状态可视化：状态栏展示当前生效模式（体验/练习/连续对话/微句流/自进化/星图）
- [ ] U-6 移动端（≤760px / 390px）验证：胶囊栏不溢出、抽屉可开合、无横向滚动