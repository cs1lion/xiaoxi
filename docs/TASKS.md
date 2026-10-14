# TASKS — Phase 2 任务看板

**维护人：Role 0（唯一可写）。** 各 Role 在汇报里报告状态变更，由 Role 0 更新本文件；执行 Agent 不得自行改写，避免并行写冲突。
**基线：** `v0.5-phase1-freeze`（commit `a6620c5`）｜契约更新：`16fceba`
**图例：** `[x]` 完成且测试全绿 ｜ `[~]` 进行中 ｜ `[ ]` 待办 ｜ `[!]` 阻塞（等 `[PROTOCOL_REQUEST]`）

## 流程与规范（Role 0）

- [x] 阶段一封盘：健康检查、DB 备份、PARKED_TASKS 盘点、21 份实验报告归档
- [x] `AGENT_PLAYBOOK.md`：角色白名单 + 四条铁律
- [x] `PROTOCOL.md` 第十至十三节：F1–F4 契约
- [x] `check_protocol.py` 第五类检查：REST 接缝（前端引用的 /api 路由必须存在）
- [x] `TASKS.md` / `WORKFLOWS.md` / `TESTING.md` 三件套 + 三个协作仪式
- [x] 打标 `v0.5-phase1-freeze`

## F1 记忆内化与日记融合（Role 1）

- [ ] 开工前协议红线自检（PROTOCOL.md 第十/十三节字段够用性）
- [ ] 按天日记合成与程序化校验（diary 表 + 向后兼容迁移）
- [ ] drift / spark 状态机与上限控制
- [ ] `persona.py` 的 `bias_block()` 纯函数 + 单测
- [ ] `diary_notice` 事件接线（`[PROTOCOL_REQUEST]` → Role 0）
- [ ] `GET /api/memory/diary` 路由（router 工厂 + Role 0 合入 app.py）
- [ ] 里程碑打标 `v0.6-phase2-diary`

## F2 Neuro 式长语音（Role 2）

- [ ] 开工前协议红线自检（PROTOCOL.md 第十一节字段够用性）
- [ ] orchestrator 微句切分 + `audio.chunk` 字段 + 可选 `speech_plan`
- [ ] 预加载合成队列（并发有界、失败跳过、index/seq 单调）
- [ ] 双工打断：`barge_in` 后取消合成、`interrupted` 后零迟到 audio
- [ ] `tts.py` 长文本入口（旧签名保持兼容）
- [ ] `timeline.js` 呼吸停顿与微表情（字段缺失时等价现状）
- [ ] `config/pacing.yaml` speech 段（默认关闭新特性时等价现状）
- [ ] 里程碑打标 `v0.6-phase2-speech-chunking`

## F3 后台自进化（Role 1）

- [ ] `assistant/loop.py` 自治模式（有界复盘 + trace）
- [ ] `self_state` 持久化与注入路径（禁 subject="user"）
- [ ] `self_evolution` 事件 + `source:"self_topic"` 主动开口接线
- [ ] `config/memory.yaml` `self_evolution` 段（默认全关）
- [ ] 里程碑打标 `v0.6-phase2-self-evolution`

## F4 星图记忆（Role 1 底层 + Role 3 前端）

- [ ] Role 1：starmap 图谱查询 + `node/{id}` 详情 + 路由工厂
- [ ] Role 3：星图数据桥接与渲染（漫游/检索/详情/narrate 状态机）
- [ ] Role 3：主工作台视图接入 diff（`[PROTOCOL_REQUEST]` → Role 0 合入）
- [ ] Integration Check：Role 1 ↔ Role 3 请求/响应对齐
- [ ] 里程碑打标 `v0.6-phase2-starmap`

## 集成与封版（Role 0）

- [ ] Integration Check：Role 1 ↔ Role 2（`bias_block` 调用点）
- [ ] Integration Check：Role 2 ↔ Role 3（`audio.chunk` 字段 / narrate 链路）
- [ ] 汇总 `[PROTOCOL_REQUEST]`，统一接线 app.py / app.js / index.html
- [ ] 接线后重跑全局门禁 + 受影响浏览器回归
- [ ] 总封版打标 `v0.6-phase2`
