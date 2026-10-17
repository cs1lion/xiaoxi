# 小溪 (Xiaoxi)

个人中文语音 Agent：台前语音快环 (System 1) + 幕后 Agent 慢环 (System 2)。

## 开工必读
- 多 Agent 协作：先读 `docs/AGENT_PLAYBOOK.md`（角色白名单、红线确认、完工标准）
- 接口契约：`docs/PROTOCOL.md`（不得自造 WS 事件 / API 字段）
- 测试命令：`docs/TESTING.md`

## 验证（改完必须跑，全绿才算完成）
- `python scripts/check_protocol.py`
- `python scripts/test_core.py`（离线，无需 API Key）
- `npm test`
- 评测：`.\.venv\Scripts\python.exe scripts\run_evals.py`（离线层，纳入黄金命令）；live 评测需要 LLM_API_KEY，用 `--live` 手动跑，换模型或改 Prompt 装配逻辑时必须跑一次
- 无 API Key 时自动进入 Mock 模式，可跑通全链路

## 不可违反的设计约束
- 性格与事实准确性冲突时，准确性优先
- 记忆检索按三档置信度装配；检索为空必须显式注入负向约束，禁止用常识脑补共同经历
- 记忆删除是 SQLite 物理硬删除，不留索引
- 快环（语音流）内不得出现阻塞调用，重活一律异步卸载到慢环
- 新增 Worker 只改 `config/agents_roster.yaml` 与适配器，调度核心零改动
- 每轮 Assistant 示例都带合法 `[情绪]` 标签

## 非目标
- 不追求在级联管线下压进 500ms 首字延迟

## 修改本文件
仅 Role 0 可改。保持在一页内，细节放进 docs/ 并从这里指过去。
