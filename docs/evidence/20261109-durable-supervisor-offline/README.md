# `durable_supervisor` 门禁：把"零外部网络"变成事实

日期：2026-11-09 ｜ 基线：`fix/db-sentinel-no-real-write` @ `c65b12e`

## 1. 口径与实现不符

`scripts/test_durable_supervisor.py` 的 docstring 写「零外部网络：只连 127.0.0.1」，
实际这一轮全量里它 FAIL：

```
FAIL 事件流以 artifact 收尾  [{'type': 'error', 'text': '执行体没交差：没有调用 submit_artifact'}]
FAIL 进度事件都在
=== 10 passed, 2 failed ===
```

sidecar 的运行台账（`docs/evidence/20261109-workspace-root-abs/durable-fail-evidence.txt`）：

```
doc 8 base {"model":{"provider":"xiaoxi","modelId":"step-3.7-flash"}, ...}
task 13 "pi.generation" terminal {"error":{"message":"400: {\"message\":\"you have no active step plan subscription\", ...}"}}
```

即：这条门禁真的向 `https://api.stepfun.com/step_plan/v1` 发了模型调用。

## 2. 哪一步碰了网络（已实测）

1. `server/assistant/durable_supervisor.py:69` `env = dict(os.environ)` —— sidecar 继承宿主整份环境。
2. `import server.assistant.durable_supervisor` 会执行 `server/assistant/__init__.py`
   → `from .store import AssistantStore` → `server/config.py:91 load_dotenv(ROOT / ".env")`。
   `.env` 里 `LLM_BASE_URL=https://api.stepfun.com/step_plan/v1`、`LLM_MODEL=step-3.7-flash`。
   （实测：`import server.assistant.durable_supervisor` 后 `os.environ` 里两者都在。）
3. `agent-runtime/src/task.ts:122-127 workerMode()`：`XIAOXI_WORKER_MODE` 未设且
   `LLM_BASE_URL`+`LLM_MODEL` 存在 → `"model"` → `deliverWithModel()` 调 pi-ai provider。
4. 该 provider 的 `baseUrl` 就是 `.env` 里的 stepfun 地址（`agent-runtime/src/models.ts:12-17`）。

## 3. 为什么昨夜 01:21 是绿的（推断，非事实）

仓库里没有任何记录 `01:21` 的产物，无法还原那一轮的实际环境变量。能确定的是：
**这条门禁只有在 sidecar 跑 scripted 路径时才会绿**——`进度事件都在` 断言
`progress == 3`，而 `deliverWithModel()` 一个 progress 都不发（失败那轮台账里
`RunEvents` 只有一条 error）。所以 01:21 那一轮要么宿主环境没有 `LLM_*`
（或被更早的设置盖住），要么显式带了 `XIAOXI_WORKER_MODE=script`。
"当时订阅可用"**不能**解释绿灯：订阅可用只会让 model 模式交差，progress 断言照样红。

## 4. 修法（选"真离线"，断言强度不降）

- `test_durable_supervisor.py`：`SCRIPT_ENV = {"XIAOXI_WORKER_MODE": "script"}`，
  传给构造 sidecar 的 `extra_env`（`_spawn` 里 `env.update(self.extra_env)`）。
  `workerMode()` 先看这个变量，因此即使继承到 `LLM_*` 也走本地 scripted 路径。
- 新增一条断言：`没有模型 token 事件（零外部网络）`（`token` 事件只在模型有 usage 时出现）。
  原有 `progress == 3` / `artifact` 收尾不动 —— 断言强度不降。
- docstring、`docs/TESTING.md`、`scripts/run_gates.py` 的门禁注释同步说明"钉 script 模式"。

不改 `DurableSupervisor` 本身：生产路径本来就需要 model 模式，继承 `os.environ`
是设计（`extra_env` 的用途就是覆盖）。

## 5. 读数

改前（当前树，直接跑）：`=== 10 passed, 2 failed ===`（见 §1）。
改后：`=== 13 passed, 0 failed ===`（完整输出见 `after.txt`）。
`before.txt` 为改前同一命令的原始输出。
