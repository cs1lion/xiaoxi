"""工作区根解析：相对值的基准是**服务数据目录**，不是进程 cwd。

为什么单独一个模块：花名册里 `AQI_WORKSPACE_ROOT` 一直是相对路径
（`data/commissions`），而两侧各自解析：

- `cli_subproc.prepare_context` 把花名册 env 原样塞进子进程 env，shim 再
  `Path(root).resolve()` 按**子进程 cwd** 解析；
- `durable._workspace()` 在服务进程里同样按 cwd 解析。

两边 cwd 一旦不同，产物写在一处、验收按另一处回读（`artifacts_exist` 判文件
不存在），看起来像"执行体没产出"；测试进程的 cwd 恰好是仓库根时，写入直接落进
真实 `data/commissions/`——2026-11-09 全量审计实测 `gate_tmp_leak` /
`step8_e2e_browser` / `drill_ws_reconnect` 三条门禁都这么漏。

**基准固定为 `config.service_data_dir()`**：生产是仓库 `data/`；哨兵进程
（`TEST_SENTINEL=1`）跟随 `DB_PATH` / `APP_DATA_DIR` / 进程临时目录。因此同一份
花名册相对值 `commissions` 在生产是 `data/commissions`、在门禁里是临时根，
两种运行各落各处，且都不再依赖 cwd。

优先级：适配器配置（`env.AQI_WORKSPACE_ROOT` / `workspace_root`）> 进程
`AQI_WORKSPACE_ROOT` > 默认 `commissions`。配置优先是为了保住花名册的
**逐 Worker 隔离**（durable 里 `workspace_root` 单条覆盖全局），进程 env 只做
兜底；显式给了绝对路径就原样采纳，不做任何改写。
"""
from __future__ import annotations

import os
from pathlib import Path

from ...config import service_data_dir

DEFAULT_ROOT = "commissions"
ENV_KEY = "AQI_WORKSPACE_ROOT"


def resolve_workspace_root(configured: object = None, *,
                           default: str | None = DEFAULT_ROOT,
                           allow_env: bool = True) -> str:
    """把花名册/进程 env 里的工作区根解析成绝对路径；`default=None` 时没配置返回空串。

    空串是 `durable._workspace()` 的既有契约：没给根 = 不允许写文件，sidecar
    不会替宿主猜一个目录。**不保留 cwd 语义**：相对值只认 `service_data_dir()`。

    `allow_env=False` 给 durable 用：花名册留空就是"不许写"，不能被宿主 shell 里
    偶然 export 的 `AQI_WORKSPACE_ROOT` 顶开——那是把"明确关闭"变成"看环境"。
    """
    raw = configured.strip() if isinstance(configured, str) and configured.strip() else ""
    if not raw and allow_env:
        from_env = os.environ.get(ENV_KEY, "")
        raw = from_env.strip() if from_env.strip() else ""
    if not raw:
        return "" if default is None else str((service_data_dir() / default).resolve())
    path = Path(raw).expanduser()
    if path.is_absolute():
        return str(path.resolve())
    return str((service_data_dir() / path).resolve())


__all__ = ["DEFAULT_ROOT", "ENV_KEY", "resolve_workspace_root"]
