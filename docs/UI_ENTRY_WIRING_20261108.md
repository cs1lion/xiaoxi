# UI 入口接线报告（2026-11-08）

分支：`feat/ui-entry-wiring-20261108`（已 rebase 到 `origin/master` 的 `c09e2a7`，不 push、不合 master）

改动提交：

- `47c67c1` feat(web): 接上记忆硬删除/历史、会话记录与评测场景入口
- `0bd9cbf` docs: UI 入口接线报告（记忆删除/历史、会话记录、评测场景）
- `d866397` docs: 更新入口接线报告到 master c09e2a7 并核对新增路由
- `cc12664` test(web): 修复新 DOM 下浏览器门禁并让 ui_browser 跑独立沙盘

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

## 五、更新到最新 master 的核对（2026-11-09）

- 基线：`git fetch origin` 后 rebase 到 `origin/master` `c09e2a7`（含 ASR 降级、检索桥接、CI 修复、程序性记忆夹具）。
- 结果：两笔提交无冲突自动重放，新 SHA `47c67c1` / `0bd9cbf`；未 reset、未覆盖任何已合入内容。
- master 在 `4658d0d..c09e2a7` 之间没有改动 `web/`、`server/app.py`、`scripts/check_protocol.py`（`git diff --stat` 只命中 `docs/TESTING.md`），所以接线代码无需适配。

重跑读数（同一 worktree、同一命令）：

| 命令 | 读数 |
| --- | --- |
| `node web/memory-panel.test.mjs` | 15 passed, 0 failed |
| `npm test` | 各子套件 TAP 合计 479 passed, 0 failed（EXIT=0） |
| `python scripts/check_protocol.py` | PASS；122 条路由，其中 `/api` 路由 93 条 |
| `python scripts/test_core.py` | 134 passed, 0 failed |
| `python scripts/run_evals.py` | 18 passed, 0 failed, 0 xfail（离线层，live 7 条按设计跳过） |

### 新增前端调用 × 路由注册核对

`check_protocol.py` 的第 5 类检查只核对 `/api` 字面量，含 `${` 的模板串会直接放过（见 `scripts/check_protocol.py:17` 与 `:416-418`）。所以四条调用分两种方式核对：

| 前端调用 | 核对方式 | 结果 |
| --- | --- | --- |
| `POST /api/memories/forget-key`（body `{key}`） | 字面量，进第 5 类检查 | 命中 `server/app.py:358`，`payload.get("key")` 一致 |
| `GET /api/memories/history?key=` | 字面量，进第 5 类检查 | 命中 `server/app.py:353`，`key` 必填 query 一致 |
| `GET /api/eval/scenarios` | 字面量，进第 5 类检查 | 命中 `server/app.py:1744` |
| `GET /api/session/${id}/transcript?limit=100` | 模板串被检查器跳过，按真实路径手工代入核对 | `/api/session/SAMPLE_SESSION_ID/transcript` 与 `server/app.py:395` 的 `/api/session/{session_id}/transcript` 匹配；`limit` 是 `int = 100` 且服务端夹在 1..200 |

机械核对输出：`literal_calls_hit` 三项均为 `(True, True)`（既在 `frontend_api_calls()` 里，也能匹配到注册路由）；`template_call_substituted True`。没有拼错路径或漏参数。

一处仍与 master 一致的事实：`GET /api/eval/scenarios` 读 `ROOT/eval/scenarios.json`，仓库里没有该文件，因此该入口当前显示空状态；这不是本次接线引入的问题。

## 六、合并预演发现的门禁修复（2026-11-09）

### 6.1 `scripts/test-voice-browser.mjs`

新 DOM 每行多了一个 `details.mem-history`（「查看这条事实的历史版本」），原来按 `details` / `summary` 的笼统断言不再成立：

- 证据展开改为 `locator('summary', { hasText: '查看原话' })`，避开 history 的 summary，消除 strict mode 冲突。
- 「无原话」断言改为 `details:not(.mem-history)` 计数为 0，并额外断言 `details.mem-history` 为 1；区分「没有证据」和「没有任何 details」。
- 修正偏好 PATCH 后的同一类断言（`:487`，合并方清单里没列到，实测也会红）。
- 「忘掉」改成两段：第一次点击后先断言 **该 id 的 DELETE 请求数为 0** 且 `.mem` 行仍在，再点「确认删除」等结果。这样「第一次点击不发删除请求」有了可跑出 pass/fail 的自动化覆盖，而不只是文案断言。

### 6.2 `scripts/test-ui-browser.mjs` 改打独立沙盘

- 默认起 `127.0.0.1:8022`（`UI_PORT` 可覆盖），显式拒绝 8000/8010；不改 `run_gates.py` 的调用命令，不影响其它门禁（各浏览器门禁本来各占一个端口，8022 此前未被占用）。
- `DB_PATH=<worktree>/tmp/ui-browser-<stamp>/memory.db`、`TEST_SENTINEL=1`，不碰用户的 `data/` 与正在运行的服务；`finally` 里关浏览器、`taskkill /T` 收服务、删临时目录。
- 解释器优先本 worktree 的 `.venv`，没有则回退主工作树的 venv（与现有 drill 脚本一致）。
- 新增来源断言：直接 `fetch /static/app.js` 与 `/static/memory-panel.js`，要求分别含 `confirmBeforeDelete` 与「删除后不可恢复」。如果谁再把它指回旧端口，这条会立刻红。
- 该门禁里原有的「忘掉」点击也补成两段，并断言第一下后记忆仍在。

### 6.3 读数（2026-11-09，分支 `feat/ui-entry-wiring-20261108`）

| 命令 | 读数 |
| --- | --- |
| `node web/memory-panel.test.mjs` | 15 passed, 0 failed |
| `npm test` | 479 passed, 0 failed（EXIT=0） |
| `node scripts/test-voice-browser.mjs` | EXIT=0，含新增「第一次点击不发 DELETE」断言 |
| `node scripts/test-ui-browser.mjs` | EXIT=0；沙盘 `http://127.0.0.1:8022`，`DB_PATH=tmp/ui-browser-<stamp>/memory.db`，来源断言通过 |

`ui_browser` 现在验的是本 worktree 的代码：服务由脚本自己 `python -m uvicorn server.app:app --port 8022` 拉起，静态文件来自 `web/`，并额外断言 `app.js`/`memory-panel.js` 含本轮新增代码。8000/8010 与主工作树全程未动。
