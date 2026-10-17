# TESTING — 黄金测试命令手册

仓库里有上百个测试脚本，Agent 汇报"我做完了"之前，**必须把本节里属于自己角色的一行命令全部跑通**，并把结果附在汇报里。命令统一用项目虚拟环境（系统 `python` 与 numpy 2.x 不兼容，会直接 ImportError）：

```bash
.\.venv\Scripts\python.exe <script>
```

## 全局门禁（所有角色，任何改动后必跑）

```bash
.\.venv\Scripts\python.exe scripts\check_protocol.py   # 协议漂移 + REST 接缝，必须 PASS
.\.venv\Scripts\python.exe scripts\test_core.py        # 基线 38 passed / 0 failed，零回归
.\.venv\Scripts\python.exe scripts\run_evals.py        # 回归评测离线层，失败即非零退出码
```

## 回归评测（evals/）

幻觉与快环类事故的固化回归，用例在 `evals/cases/*.yaml`，一文件一用例，runner 是 `scripts/run_evals.py`。

- **offline 层**：无网络、无 Key、确定性，记忆库强制 `:memory:`。已纳入上面的全局门禁与 CI，失败即非零退出码。涉及记忆装配 / Prompt / 模型切换的改动必须跑。
- **live 层**：真实模型，需显式 `--live` 且配置 `LLM_API_KEY`；无 Key 或 Mock 模式时自动跳过并明示，退出码仍为 0（不算失败）。换模型或改 Prompt 装配逻辑时手动跑一次，每次运行把每条用例每次的原始回复和判定写入 `evals/results/`（gitignore，只存回复与判定，不存 prompt 与凭据），并打印通过率表。live 的「是否编造」第一版用正则判断，有误判风险，必须抽查落盘的原始回复。
- **xfail**：`status: xfail` 的用例照常运行并在汇报中列出，但不计入失败（必须写 `reason`）。
- **新增用例**：每次出幻觉事故就加一条 YAML。字段见 `evals/cases/` 现例；`checks` 支持 `contains / not_contains / regex_any / not_regex / custom`，custom 判定函数写在 `evals/checks.py`，用例里以 `evals.checks:<函数名>` 引用。预置状态写 `setup.memories`，用户轮次写 `turns`，live 默认跑 `runs: 3` 次，通过线由 `pass_rule: all | min_rate:0.8` 控制。`origin` 必填（来源实验 / 事故 / commit）。

## V3 内阁总管（全员适用，2026-10-15 起）

```bash
.\.venv\Scripts\python.exe scripts\test_executive_v3.py         # 督办台账 / 观测网关 / 自进化初审 / 台前意图 + ui_nav 领航载荷
.\.venv\Scripts\python.exe scripts\test_agent_adapter_ext.py    # Worker 适配器可插拔（Mock 适配器跑通全程）
node web\executive-state.test.mjs                               # 心流防打扰纯逻辑（零 DOM）
node web\ui_nav.test.mjs                                         # 界面自动领航（ui_nav）纯逻辑 + 心流护栏（零 DOM，无服务依赖）
node scripts\test-ui-nav-browser.mjs                             # 领航浏览器回归：抽屉自动开合 / 聚焦 / 子动作 / 侧栏·星图落点（需先启动本地服务；服务未起时自动 SKIP，不算失败）
```

> **改动 `web/` 前端文件后**：必须杀掉旧服务进程再重启（`taskkill //F //PID <持 8000 端口的进程>`
> 然后 `.\.venv\Scripts\python.exe scripts\run.py --port 8000`），否则 `StaticFiles` 缓存的目录
> 列表会让新增的前端文件（如 `web/ui_nav.js`）持续 404，浏览器回归会假失败。

`check_protocol.py` 在 V3 起多管两类：**服务端自用路由**（`/internal/telemetry_proxy`）与
**ui_nav 领航取值域**（协议§二十四，前后端取值不漂移）。
前端不引用前者，所以第五类检查看不见；改了路径会让 Worker 的调用静默 404，
因此显式列在 `INTERNAL_ROUTES` 里，契约一改静态检查直接报警。

跑 V3 相关改动时，上面五行加全局门禁两行全部跑通才算完。

## Role 1（记忆 / 日记 / 自进化 / 星图底层）

```bash
.\.venv\Scripts\python.exe scripts\test_memory_api.py            # 记忆 REST 契约
.\.venv\Scripts\python.exe scripts\test_memory_orchestrator.py   # 记忆 × 对话编排
.\.venv\Scripts\python.exe scripts\test_memory_store_contract.py # 存储层契约
.\.venv\Scripts\python.exe scripts\test_memory_evidence.py       # 证据链
.\.venv\Scripts\python.exe scripts\test_memory_retrieval.py      # 检索
.\.venv\Scripts\python.exe scripts\test_memory_preference.py     # 偏好合同
.\.venv\Scripts\python.exe scripts\test_agent_loop.py            # assistant/loop.py
.\.venv\Scripts\python.exe scripts\test_episodic_diary.py        # 第一阶段新建：日记/偏置/火花/星图
.\.venv\Scripts\python.exe scripts\test_cross_day_companion.py   # 跨天陪伴三天验收：Day1 共建 / Day2 恢复 / Day3 续做与修正
```

`test_cross_day_companion.py` 验的是**管线与状态机**：自然语言「接着昨天的继续」走对路、
事项跨重启恢复、决定/问题/进展在口述里可区分、修订产生版本血缘而非平行决定、
旧决定永不被当作当前决定复述、稿子版本不悄悄切换。它**不**证明真实模型的语义能力
（resolver 是脚本化的）——真实 LLM 的输出质量需另做离线评测，与
`docs/EVOLUTION_QUALITY_EVAL.md` 同类的「机制通过 ≠ 语义合格」声明。

数据库结构有变更时，改动前后各跑一次：

```bash
.\.venv\Scripts\python.exe scripts\backup_local_databases.py
```

## Role 2（长语音 / 微句流 / 演出编排）

```bash
.\.venv\Scripts\python.exe scripts\test_voice_turns.py           # 轮次协议（turn_id/打断/取消）
.\.venv\Scripts\python.exe scripts\test_reply_delivery.py        # 固定回复交付器
.\.venv\Scripts\python.exe scripts\test_speech_performance.py    # 动作意图与锚点
.\.venv\Scripts\python.exe scripts\test_speech_performance_route.py
.\.venv\Scripts\python.exe scripts\test_stream_delivery.py       # 流式交付
.\.venv\Scripts\python.exe scripts\test_micro_delivery.py        # 第一阶段新建：微句/预加载/打断（假 TTS，不碰真实供应商）
.\.venv\Scripts\python.exe scripts\test_ambient_tiers.py         # 三梯度分级 / 退避阈值纯逻辑
.\.venv\Scripts\python.exe scripts\test_ambient_orchestration.py # 三梯度编排：破冰兜底 / 首次阈值 / 退避 / 真声
node scripts\test-speech-performance-browser.mjs                 # 浏览器回归：受控 WS + 生成 WAV
node scripts\test-ambient-browser.mjs                            # 三梯度运行链路：micro_act / mumble 气泡 / 接话
npm run test:voice                                                # 语音模块 + 播放 + 双工 + 捕获 + 检查点
```

## 陪伴心流三态胶囊（打磨轮 2026-10-16）

主动开口疏通 + 双 Switch → 三态胶囊重构的回归命令：

```bash
.\.venv\Scripts\python.exe scripts\test_ambient_tiers.py          # 首次阈值 28s / voice_backoff 指数退避
.\.venv\Scripts\python.exe scripts\test_ambient_orchestration.py  # 冷启动破冰 / 梯度3 陪伴接管 / 授权门
node scripts\test-presence-capsule-browser.mjs                   # 三态循环 / 配置派发 / REST 持久化回滚 / 心跳门控
node scripts\test-ui-browser.mjs                                 # UI 契约（含胶囊焦点与失败回滚）
node scripts\test-voice-browser.mjs                              # 语音轮次（配置推送改由胶囊触发）
```

## 动态积木 · Voice-First 交互范式（协议 §23，打磨轮 2026-10-18）

小溪默认交互升级为"简短口语语音 + 动态生成式 UI 积木"（去文字框化）。回归命令：

```bash
.\.venv\Scripts\python.exe scripts\check_protocol.py             # 七类漂移（含积木 kind 三方认账）
.\.venv\Scripts\python.exe scripts\test_aqi_blocks.py            # 流式标签过滤 / 组装器 / 路由直出 / 分流 / 七种 kind 契约（49 例）
.\.venv\Scripts\python.exe scripts\test_voice_turns.py           # 轮次协议（aqi_action 入口 / 星图摘录 / §23.6 主动随路积木）
.\.venv\Scripts\python.exe scripts\test_stream_delivery.py       # 流式交付（积木先于 done 下发）
npm run test:blocks                                              # 积木纯函数：parseCmd 白名单 / 信封校验 / kind 与渲染器一一对应
node web\aqi-flow.test.mjs                                       # 心流防打扰：打字期间挂起、报数、停手后原序补弹（零 DOM）
node scripts\test-aqi-blocks-browser.mjs                         # 7 种积木 DOM / 五类 cmd / nav+drawer_ops / gaze / 上限 / XSS
node scripts\test-ui-browser.mjs                                 # UI 契约（去文字框化：微光接手，无 bot 气泡）
node scripts\test-voice-browser.mjs                              # 语音轮次零回归
```

## Role 3（星图前端 / 工作台）

```bash
node web\starmap\test_starmap.mjs        # 第一阶段新建：数据桥接/检索去抖/narrate 状态机/空态
npm run test:voice                       # 播放链路零回归
node scripts\test-workspace-browser.mjs  # 主工作台零回归
node scripts\test-ui-browser.mjs         # UI 契约零回归
```

浏览器手验（桌面 + 移动视口）不属于自动测试，但验收标准里要求：星图非空白、节点可点、讲述可播放可打断，附截图。

## 不属于回归的脚本（按需手动，别塞进验收）

`scripts/eval_*.py` 与 `scripts/probe_*.py` 会调用真实模型/供应商或有固定预算，属于评测不是回归；默认干跑（0 次真实调用），`--live` 需显式配置和预算，验收时不要跑。

## 回归失败时

不允许"已知失败"式汇报。先判断是不是自己改出来的：`git stash` 自己的改动重跑对照（由 Role 0 操作），确认基线后修到全绿再汇报。
