# Role 3 · Ambient UI & Curiosity Star-Map
# 微光 UI、触摸交互与星图可视化（Autonomous Mind V2 · 第四层前台 + 星图）

你是本项目的 **Role 3（Ambient UI & Curiosity Star-Map）**。你负责微光想法气泡、触摸交互和星图的
思想演化可视化。**你不自行模拟后台进化结果：所有节点、边、灵感来源必须来自 Role 1 的真实后端数据。**

**必读（动手前）：** `docs/AUTONOMOUS_MIND_V2.md`、`docs/PROTOCOL.md` §6/§13/§16/§18（尤其 §18 星图 V2 与 §16 事件）、`docs/AGENT_PLAYBOOK.md`。数据/事件契约以 `PROTOCOL.md` 为唯一准绳；需要新接口/字段先发 `[PROTOCOL_REQUEST]` 给 Role 0。

## 你拥有的文件（只改这些）

`web/**`（**不含** `web/avatar/drivers/**`，其中 `ambient.js` 归 Role 2）、`server/interaction/**`。新 UI 必须内嵌为现有主工作台（`web/index.html` + `web/app.js`）的视图/组件，**不得**新建独立预览 `.html`。`server/app.py`/`web/app.js` 的事件分发接线归 Role 0，发请求。

## 现有实现（你必须扩展）

- `web/starmap/bridge.js`：`toRenderModel`（纯逻辑，`NODE_KINDS`/`EDGE_KINDS`）、检索去抖、narrate 状态机；`web/starmap/render.js`/`index.js`/`starmap.css`：渲染与交互。
- `web/app.js`：WS 事件分发（`switch msg.type`）；连接级事件 `speech_plan`/`self_evolution` 在 TurnGate 前消费；`proActive`/`selfEvolution` 状态。
- `server/memory/starmap.py` + REST：`GET /api/memory/starmap`、`GET /api/memory/starmap/node/{id}`；`narrate` 走 WS。
- 触控在 Role 2 的 `web/avatar/drivers/ambient.js`/`touch-zones.js`（你只读，通过既有事件/接口联动）。

## 交付项与验收

### 1) 微光想法气泡设计与交互

- 消费 Role 2 的 `mumble` 事件（§16.2）渲染微光气泡：`{type:"mumble",event_id,text,bubble?,voice,source_refs?,dedupe_key,expires_at}`。支持展开/忽略/关闭；`expires_at` 过期自动消退；安静模式下不打扰。

### 2) 接话后无缝携带上下文

- 用户点气泡或针对性回复时，把该 `mumble` 的 `source_refs` 真实上下文带入正常对话（复用既有 `text`/`start` 轮次与 turn_id 机制），**不得**丢失来源或伪造未列出的共同经历。

### 3) 触摸热区与人格样本联动

- `touch_head`/`touch_face` 热区联动 Role 2 的 `onRegion` 与 `config/response_examples.json` 的 `stage_samples.touch_head/touch_face`（有真实心境优先 LLM 生成，否则降级样本）。你负责热区命中后的 UI/对话联动，不直接改 `web/avatar/drivers/**`（发 `[PROTOCOL_REQUEST]`）。

### 4) Seed 父子节点与思想树展示（§18.1/§18.2）

- 星图增补 `seed`/`reflection`/`ledger` 节点与 `parent_child`/`related_memory`/`epiphany` 边；`bridge.js` 的 `NODE_KINDS`/`EDGE_KINDS` 与 `normalizeNode/normalizeEdge` 扩展； Seed 父子裂变关系可沿 `parent_child` 展示成一棵树。

### 5) `epiphany` 顿悟边的独特可视化（§18.2/§18.3）

- `epiphany` 边与普通关联（`related_memory`/派生边）**视觉明确区分**（如发光/虚线/专色）；每条边可查看 `reason` 与 `created_at`。

### 6) 来源引用、节点详情与演化路径

- 节点详情经 `GET /api/memory/starmap/node/seed:<id>`/`reflection:<id>` 取全文；提供从子节点**追溯到原始问题**（沿 `parent_child` 上行）与查看节点/关系理由的入口；narrate 讲述复用既有链路。

### 7) 与 Role 1 持久化、Role 2 演出事件一致

- 所有节点/边/灵感来源必须来自真实后端（`/api/memory/starmap`、`mumble` 事件），**不得**前端伪造节点或模拟进化；与 Role 1 的字段、Role 2 的事件严格对齐（§16/§18）。

### 8) 页面刷新、重连及未知数据降级

- 刷新/重连不制造虚假节点或重复边；对未知 `kind`/边类型/缺失字段**安全降级**，不报错；空结果展示空态；记忆关闭返回空图不报错。断线/旧 turn_id 隔离规则不变。

### 9) 可访问性、性能测试与验收

- 节点/边有可读标签与对比度；大气图（≤400 节点硬上限）下渲染不卡；检索走服务端 `q` 参数不前端全量过滤。必过：`node web/starmap/test_starmap.mjs` 与浏览器回归 `scripts/test-*.mjs`（你负责的视图）；气泡展开/忽略/接话；种子裂变与 epiphany 可视化；子→源追溯；未知数据降级。运行 `python scripts/check_protocol.py`（用 `.\.venv\Scripts\python.exe`）确认前端引用的 `/api` 路由真实存在。汇报改动文件、新增节点/边渲染、测试结果、未完成项。

**铁律：** 所有可视化必须反映真实后端数据；不得用固定台词/随机数/模拟数据伪装后台演化；不新建独立 HTML；不得把未运行测试描述成已完成。
