# 像素世界分家边界记录

- 日期锚：2026-11-05
- 执行基线 HEAD：`9bf1456`
- 性质：**版本控制层剥离 + 产品入口回正，零协议变更**。`docs/PROTOCOL.md` 全程未动，未删任何 WS 事件或 API 字段。
- 内核去向：渲染与模拟内核已于 2026-10-25 迁到独立仓 `cs1lion/pixel-world-engine`
  （见 `pixel-world/MIGRATED.md`）。

## 第一步：版本控制层剥离（本次不动 server/、web/）

- `git rm -r --cached pixel-world/`：把主仓 git 树里的 175 个世界源码文件从索引剥离。
- 磁盘上的 `pixel-world/` 目录不删除，本机副本保留作历史契约查证：
  `scripts/check_protocol.py` 的 `check_world_events` 仍读本机 `ledger.ts` 校验
  `commission_update` 事件名；`scripts/test_pixel_world.py` 有副本时仍照原样真跑。
- CI 侧：`.github/workflows/ci.yaml` 删除 `Install pixel-world dependencies` 步骤；
  `scripts/test_pixel_world.py` 无目录时按设计 SKIP（打印原因、exit 0），不再 FAIL。
- 回退：`git add -f pixel-world/` 即可恢复跟踪。未 commit、未 push、未删磁盘文件。

## 第二步（同日补做）：产品入口回正

分家暴露出一处真实破损：2026-10-24 的「入口反转」把 `/` 设成世界页
（`server/app.py` → `world_entry_response` → 无 dist 时是 `server/static.py:45-55`
的「世界还没建好」说明页）。新 clone 不再有 dist，意味着**打开首页只能看到一张
报错说明页**，语音陪伴被挤到 `/chat`。

- `server/app.py`：`/` 改为返回 `web/index.html`，`/chat` 保留为同一页面的兼容别名；
  `/world` 仍由 `mount_pixel_world` 在有产物时条件挂载。
- `server/static.py`：`pixel_world_index` / `world_entry_response` 的 docstring 更正为
  「不再是产品前门」，helper 本身保留（历史契约查证 + 离线测试覆盖）。
- `scripts/test_pixel_world_hosting.py`：把过期的「守入口反转」断言改成「守世界产物
  载荷」，新增 `check_voice_companion_is_the_front_door()` 用 TestClient 真打生产
  路由，断言 `/` 与 `/chat` 都是语音陪伴页、不是世界页也不是说明页。
- 只改页面路由，不碰 WS/API 字段；`check_protocol.py` 的 `server_routes()` 只收
  `/api/`，不受影响。

## 仍活跃耦合在主仓的 server 侧清单（后续待解）

- `server/world_intent.py`：`/api/world/intent`
- `server/static.py`：`/world` 条件挂载（`/` 已不再指向世界）
- `server/app.py:1273`：`commission_update` 广播
- `server/assistant/service.py`：`world.dispatch` / `world.status`
- `server/contract/events_v2.py` 与 `server/broadcast/v2.py`
- `docs/` 下 30 份世界相关文档

## 待 Role 0 拍板（2026-11-05 复查后收窄为 2 项）

- seam ADR 选项：主仓与独立仓之间的接缝契约如何定（`docs/ADR_DRAFT_WORLD_SEAM.md`）。
- 是否摘掉 `scripts/run_gates.py` 的 `world_bridge` 条目。**建议不摘**：S5 服务端桥
  仍在，摘掉等于少一层保护，与「分家」不是同一件事。
- ~~是否用开关把 `/` 根路由改回语音陪伴页~~ → 已在第二步执行，无需开关
  （直接改页面路由即可，零协议变更、可用 TestClient 离线验证）。

## 明确保留

- **未改 `docs/PROTOCOL.md`，未删任何 WS 事件或 API 字段。**
