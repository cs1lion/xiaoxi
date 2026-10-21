# S5 D 腿验收卡：完成/交付

- **状态**：**未达成**（2026-10-21，tag `pixel-world-baseline-20261021` 记录）
- **Owner**：Agent Integration（主产品 worker 上下文/提示词装配）；QA 独立复验；Role 0 合并
- **不是**：像素世界渲染/投影缺陷；世界侧 A/B/C/E/F/G/H 已通过
- **检查器**：`pixel-world/scripts/check-s5-d.mjs`
- **证据目录**：`pixel-world/eval/screens/`（gitignored，按纪律不入库）

## 1. 已确认的根因

隔离环境的真实 worker 单（round-07/08）在**世界侧**完整走通：任务出现 →
`saluting → working` → 工坊 `operating`，worker 也真的产出了文件；但初审按 token
预算正确打回：

- 单次调用 Prompt Tokens **421,413**，上限 **120,000**（3.5×）
- 3 次尝试累计 Prompt Tokens **1,017,073**，整单预算线 400,000（2.5×）
- `audit.findings` 含 `prompt_token_bloat`（major），status=`blocked`、retry_count=3

主产品已在基线内包含 `c9ef30a fix(budget): separate per-call context quality from
commission cost`：

- `max_prompt_tokens_per_call: 120000` 是**单次质量闸**；
- `max_total_prompt_tokens: 400000` 是**整单成本告警**，不再用累计值打回。

因此 D 的剩余动作不是“放宽预算”，而是**把 worker 单次上下文裁剪到 120K 以内**。
像素世界侧没有可修的代码路径；改世界不能改变 worker prompt。

## 2. 复跑命令（需要 `LLM_API_KEY` + 可用 worker 网关）

```bash
# 1) 隔离实例：临时 server 8001 + 临时 Vite 5189，真实 LLM key；不碰 8000/8010
cd D:/project/neurosama/tmp/wt-pixel-world
node pixel-world/scripts/s5-e2e-serve.mjs

# 2) 用现有 browser harness 跑 D 驱动（eval 下的 round-10 脚本）
#    page: http://127.0.0.1:5189/?ledger=on
#    任务：让像素世界接一单：写一个 Python 脚本，统计某个目录下所有 .py 文件的行数
#    批准确认门，等待台账终态与 delivered

# 3) 证据落盘后检查（不跑模型，只判 pass/fail）
cd pixel-world
node scripts/check-s5-d.mjs eval/screens/s5-e2e-evidence-d3.json
```

> 注意：`s5-e2e-drive-r10.mjs` 是本地浏览器 harness 脚本，当前在 `eval/`（gitignored）；
> 复跑时必须确认 `?ledger=on`、真实 WS、未注入 `pushLedger`。live 复跑会产生 API 费用，
> 由 Owner 在授权环境执行，不能在 CI 里自动跑。

## 3. 通过标准（checker 逐条断言）

| # | 断言 | 通过条件 |
|---|---|---|
| 1 | commission_id | 非空，且与台账单一致 |
| 2 | 终态 | `done` / `ready_to_report`；若是后者，必须有老板批准后的 `done` |
| 3 | 世界交付 | `worldFinal.ledgerDeliveries ≥ 1` |
| 4 | 世界动作 | timeline 或终态出现 `delivering` / `toAltar` |
| 5 | 单次预算 | `max_prompt_tokens_per_call ≤ 120000` 且 >0 |
| 6 | 审计 | 无 `prompt_token_bloat` finding |
| 7 | 整单成本 | `prompt_tokens > 400000` 只记告警，不阻断 |

自检：

```bash
cd pixel-world
node scripts/check-s5-d.mjs --self-test
# 期望：good 6/6 通过；blocked 旧证据 5 项失败；self-test passed
```

## 4. 关闭条件

1. Owner 在授权环境用同一单复跑，单次 prompt ≤120K，D 证据通过 checker；
2. QA 独立读证据文件，确认 `?ledger=on`、真实 WS、无注入；
3. 世界侧不需要改协议/写台账；D 通过后更新 S5 验收矩阵与 tag，不重写已推送的旧 tag；
4. 若仍超 120K：继续裁 worker 上下文（系统指令/工具目录/任务上下文），**不得**用放宽预算或
   世界侧假交付来关闭。

## 5. 与世界侧的关系

- 世界侧 D 的强证据已经拿到：真实 worker 在飞时，角色真的去工坊干活；缺的只是
  “台账 ready_to_report → 老板 accept → 世界 delivering/toAltar”的最后一腿。
- 世界团队不应为了让 D 变绿而改台账、改协议或伪造动作；这条卡点留在主产品线。
