# 功能探索：从装饰卡片到任务推进对象

## 本批落地

任务侧栏已接入真实 task-card.js（不是 card_preview 演示）：
- 中文状态，保存下一步行动；开始、等待、完成、重新打开。
- 指定未来时间创建与 task_id 绑定的提醒，以任务下一步/标题作内容。
- 展示关联提醒；完成任务取消待投递关联提醒；重新打开不复活旧提醒。
- 更新携带 version；冲突说明并保留草稿。提醒失败重试复用同参数 request_id。
- 展开的编辑器暂停轮询重绘，避免写了一半的草稿丢失。
- 使用柔和实体卡片皮肤，不再推进强光泽方向。

使用已有 store/API，无新框架，无数据库迁移，不把用户倾诉自动建任务。手动操作是用户明确点击；模型写操作仍遵守原确认流程。

## 开源参考与取舍

- [Vikunja Features](https://vikunja.io/features/)：任务带提醒、子任务与多视图。此处先复用下一步字段与任务关联提醒，不立即复制看板、团队、多级项目或循环任务。
- [Open WebUI Notes](https://docs.openwebui.com/features/notes/)：独立持久笔记、上下文附加、笔记工具。后续候选是“明确保存资料→带来源检索→行动项草稿”；不是隐式记忆再套一层。官方无服务端笔记历史，未来实现应自行考虑版本与恢复。
- [Khoj Search](https://docs.khoj.dev/features/search/) / [Query Filters](https://docs.khoj.dev/miscellaneous/query-filters/)：限定文件/词/文内日期范围的搜索。后续先做有限本地资料与来源，不迁入整套向量框架。自托管也可能配置远端 embeddings，不能默认全链路本地。

仅学习已阅读官方内容中的思路，不复制上游代码或声称完整实现这些项目。

## 验证

- node scripts/test-task-card-browser.mjs：10 项浏览器测试通过，使用有状态 API fixtures。下一步保存、状态变化、关联提醒、完成/重开、冲突保留草稿；无脚本错误。
- Python scripts/test_assistant_ui_contract.py：真实 FastAPI router + 隔离 SQLite，通过 next_action/状态写入、版本冲突拒绝、关联提醒取消、重开不复活提醒等。
- 截图 eval/ui/tasks/progress-card.png，报告 eval/ui/tasks/results.json。该截图是测试数据，不是生产任务。
- 无真实模型请求，无生产数据写入。

## 仍需推进

追加完整隔离联调：`node scripts/test-task-card-integration.mjs` 已通过 7 项检查。真实浏览器加载 task-card.js，通过 Playwright HTTP 传输桥接实际 FastAPI TestClient 与临时 SQLite（不是 JSON 状态模拟），确认下一步持久化、关联提醒、完成取消、重开不复活、真实并发版本冲突及草稿保留。报告为 eval/ui/tasks/integration-results.json。测试无新监听服务器，不重启原进程；临时数据库保留在系统 temp 目录便于诊断。

原运行服务 /api/preferences/proactivity 曾返回 404，当前生产监听进程尚未验收或重启。这不再阻止验证当前源码的完整隔离功能链路，但不代表运行服务已部署更新。

任务卡片已经支持实际接口，不代表自然语言模型语义正确或提醒关闭网页后可靠推送。服务关闭不调度，网页关闭仅服务器持久化收件箱，沿用现有边界。
