# UI 入口接线报告（2026-11-08）

分支：`feat/ui-entry-wiring-20261108`（基于 `master` 的 `4658d0d`，不 push、不合 master）

改动提交：

- `517c883` feat(web): 接上记忆硬删除/历史、会话记录与评测场景入口
- 本报告单独提交（见 `git log --oneline feat/ui-entry-wiring-20261108 -2`）

## 一、先核实：四条缺口的实际情况

| 盘点项 | 核实结果 | 处理 |
| --- | --- | --- |
| `POST /api/memories/forget-key` 无 UI | 属实。`memory-panel.js` 里连「这个入口在哪」的注释都没有，前端无任何调用 | 已接线 |
| `GET /api/memories/history?key=` 无 UI | 属实。记忆面板只有「显示历史和待核对记录」按状态列行，没有按 key 看版本链 | 已接线 |
| `GET /api/session/{id}/transcript` 无 UI | 属实。`web/` 下无任何 transcript 调用 | 已接线 |
| `GET /api/eval/scenarios` 无 UI | 属实。`web/` 下无任何 scenarios 调用 | 已接线 |
| 星图 `loadGraph()` 无调用方 | **不属实，已是接线状态**。`web/app.js` 的 `mountStarmap` 调 `createStarmap`，打开星图时执行 `client.loadGraph()`；`index.html` 也有 `starmapToggle` 按钮 | 未改动 |

另外核实到：`GET /api/eval/scenarios` 在服务端读 `ROOT/eval/scenarios.json`，而当前 master 上没有 `eval/` 目录（评测数据在 `evals/`），所以这个接口现在稳定返回空列表。UI 已按真实情况显示「服务端没有可用的评测场景（eval/scenarios.json 未加载）」，没有伪造数据。

## 二、接了哪几处、入口在哪、怎么用

### 1. 记忆面板 · 忘掉整条事实（`POST /api/memories/forget-key`）

- 入口：右侧「记忆与偏好」面板 → 任意**历史 / 待核对**记忆行 → **「忘掉整条事实」** 按钮。
- 为什么只在非 active 行出现：active 行原本的「忘掉」调 `DELETE /api/memories/{id}`，本来就删整条身份链；再放一个 forget-key 是重复按钮。非 active 行原先没有任何删除入口（只有一句解释），这里正好补上。
- 用法：点按钮 → 行内展开确认条「删除「<key>」的所有版本（含当前生效的一行）？删除后不可恢复。」→ 再点 **「确认删除」** 才发请求；点「取消」收起。
- 代码：`web/app.js:1668`（入口与请求）、`web/memory-panel.js:92`（入口判定）、`web/memory-panel.js:105`（确认文案）。

### 2. 记忆面板 · 按 key 查看历史版本（`GET /api/memories/history?key=`）

- 入口：右侧「记忆与偏好」面板 → 每条有 `key` 的记忆行下方 → **「查看这条事实的历史版本」**（`<details>`，点开才请求）。
- 用法：展开后按时间顺序列出状态、值、记于日期；没有日期就不显示「记于」，不编造。读取失败会显示原因并可重试（下次展开重新请求）。
- 代码：`web/app.js:1494`；数据整形在 `web/memory-panel.js:112`。

### 3. 两条物理硬删除都加了二次确认

- 改动前：active 行的「忘掉」是一次点击直接 `DELETE`，没有任何确认。
- 改动后：所有记忆删除（`DELETE /api/memories/{id}` 与 `POST /api/memories/forget-key`）都走同一条确认交互：第一次点击只展开确认条，文案写死「删除后不可恢复。」，再点「确认删除」才发请求。
- 全局「清空」沿用原有 `confirm("清空后无法恢复…")`，未改动。
- 代码：`web/app.js:1459`，文案常量 `web/memory-panel.js:79`。

### 4. 工作台 · 会话记录与评测场景（调试者入口）

- 入口：右上「工作台」抽屉 → **「诊断记录」** 区块（在「等老板拍板」之后、「督办清单」之前）。
- **「载入本次会话记录」**：调用 `GET /api/session/{session_id}/transcript?limit=100`，列出最近消息（你 / 小溪 + 模式标签）。`session_id` 取最近一帧 `ready` 给的值，断线续接后会跟着更新；还没有会话 ID 时提示先等连接就绪。
- **「载入评测场景」**：调用 `GET /api/eval/scenarios`，列出场景 id、名称、示例用户输入、检查项数量。**只读，不执行场景**。服务端文件缺失时显示空状态说明。
- 代码：`web/index.html:259-267`（结构）、`web/app.js:1908` / `web/app.js:1938`（请求与渲染）、`web/almanac.css:503`（样式）。

## 三、边界与回退点

- 只改前端：`web/app.js`、`web/index.html`、`web/almanac.css`、`web/memory-panel.js` 与其单测。
- 未改 `server/app.py`、未新增/修改 WS 事件或 API 字段、未碰 `executive.py`、未启动或重启任何服务。
- 复用的都是既有接口：`POST /api/memories/forget-key`、`GET /api/memories/history`、`GET /api/session/{id}/transcript`、`GET /api/eval/scenarios`。
- 可回退点：
  - 记忆面板两块（删除确认 + 历史展开）集中在 `web/app.js:1459-1691`，回退该段并删 `memory-panel.js` 的新增导出即可。
  - 诊断记录整块集中在 `web/index.html:259-267` 与 `web/app.js:1889-1967`，删掉即完全消失，不影响其他抽屉视图。
- 没有改动任何持久化数据；删除入口只在用户二次确认后才发请求。

## 四、验证

在 worktree `D:\project\neurosama-wt-ui` 内：

- `node web/memory-panel.test.mjs`：15/15 通过（新增 4 条覆盖删除文案、forget-key 入口判定、历史整形）。
- `npm test`：通过（worktree 首次运行因无 `node_modules` 缺 `three`，用主工作树的 `node_modules` 建 junction 后全绿；junction 未入库）。
- `D:\project\neurosama\.venv\Scripts\python.exe scripts\check_protocol.py`：PASS（122 个 /api 路由与前端引用一致）。
- `D:\project\neurosama\.venv\Scripts\python.exe scripts\test_core.py`：134 passed, 0 failed。
- `D:\project\neurosama\.venv\Scripts\python.exe scripts\run_evals.py`：18 passed, 0 failed（离线层）。
- `node --experimental-default-type=module --check web/app.js`：语法通过。

未做浏览器实测：按要求不重启 8000/8010，而运行中的服务读的是主工作树的 `web/`，不是本分支。静态交互均为原生 `fetch` + DOM，已用纯函数单测覆盖文案与入口判定。
