"""无痕热修改：config/*.local.yaml 覆盖与原子热重载（协议 §17）。

默认 < 本地覆盖。监听本地覆盖文件 mtime，变化后重新解析校验，通过就以原子方式
切换新快照；校验失败继续用上一份有效配置并记内部错误日志。不因配置损坏退出服务，
不打印“配置已更新”等用户可见提示，不把本地敏感内容打进普通日志。

未 install 前 snapshot() 返回 None，config._live 因此回落到启动加载的基础配置，
行为与改造前逐字节一致。
"""
from __future__ import annotations

import logging
import threading
import time
from pathlib import Path
from typing import Any

from .config import CONFIG_DIR, _deep_merge, _load_yaml

logger = logging.getLogger(__name__)

MANAGED = ("persona.yaml", "memory.yaml", "pacing.yaml", "avatar.yaml",
           "agents_roster.yaml", "seeds.yaml")
LOCAL_SUFFIX = ".local.yaml"

_lock = threading.Lock()
_snapshots: dict[str, dict[str, Any]] = {}
_mtimes: dict[Path, float] = {}
_installed = False
_warned_orphans: set[str] = set()
_watcher: threading.Thread | None = None
_stop = threading.Event()


def warn_orphan_overrides() -> list[str]:
    """点名"写了但不生效"的 `.local.yaml`。

    `MANAGED` 之外的文件即使存在也不会被合并。协议 §17 的覆盖范围里还列了
    `response_examples.json`，它不是 YAML（`_deep_merge` 处理不了），至今
    也没有覆盖通道——写一份 `response_examples.local.yaml` 只会静默失效。

    这里不改变"哪些文件生效"——那是产品决策——只把静默失败变成一次明确告警。
    返回本次新发现的孤儿文件名；每个文件只报一次，不每 tick 刷屏。

    2026-10-18：`seeds.yaml` 已补进 `MANAGED` 并改走 `seeds_cfg()`，
    不再是孤儿；这个函数留着守剩下的那个。
    """
    orphans: list[str] = []
    for path in sorted(CONFIG_DIR.glob("*" + LOCAL_SUFFIX)):
        managed = path.name[:-len(LOCAL_SUFFIX)] + ".yaml"
        if managed in MANAGED or path.name in _warned_orphans:
            continue
        _warned_orphans.add(path.name)
        orphans.append(path.name)
        logger.warning(
            "config_hot: %s 不在热重载覆盖范围内（MANAGED=%s），这份覆盖不会生效。"
            "要么把它加进 MANAGED 并让读取方走 *_cfg()，要么删掉它。",
            path.name, ",".join(MANAGED))
    return orphans


def _local_path(name: str) -> Path:
    stem = name[:-len(".yaml")] if name.endswith(".yaml") else name
    return CONFIG_DIR / (stem + LOCAL_SUFFIX)


def _read_local(name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Parse the local override; (None, None) when absent, (None, error) on failure."""
    path = _local_path(name)
    try:
        if not path.exists():
            return None, None
        import yaml
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:  # malformed/unsafe YAML -> keep the last valid config
        return None, type(exc).__name__
    if data is None:
        return {}, None
    if not isinstance(data, dict):
        return None, "local_override_not_object"
    return data, None


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _validate(name: str, merged: dict[str, Any]) -> bool:
    """A merged snapshot is valid when it is a non-empty object (base shape kept)."""
    return isinstance(merged, dict) and bool(merged)


def compute(name: str) -> dict[str, Any]:
    base = _load_yaml(name)
    local, error = _read_local(name)
    if error is not None:
        logger.warning("config_hot: ignoring invalid local override for %s (%s)", name, error)
        return base
    return _deep_merge(base, local) if local else base


def _merged_or_error(name: str) -> tuple[dict[str, Any] | None, str | None]:
    """Merged snapshot, or (None, error) when the local override fails to parse."""
    local, error = _read_local(name)
    if error is not None:
        return None, error
    base = _load_yaml(name)
    return (_deep_merge(base, local) if local else base), None


def snapshot(name: str) -> dict[str, Any] | None:
    """Current merged snapshot, or None when hot-reload is not active. Read-only."""
    with _lock:
        return _snapshots.get(name)


def install() -> list[str]:
    """Activate hot-reload: compute and store all snapshots, record mtimes."""
    global _installed
    with _lock:
        for name in MANAGED:
            _snapshots[name] = compute(name)
            _mtimes[_local_path(name)] = _mtime(_local_path(name))
        _installed = True
    warn_orphan_overrides()
    return list(MANAGED)


def refresh() -> list[str]:
    """Re-scan local overrides; atomically swap only the changed, valid snapshots.

    Debounced by mtime: unchanged files are skipped. A file that fails validation
    keeps its previous snapshot. Returns the names actually swapped.
    """
    swapped: list[str] = []
    with _lock:
        for name in MANAGED:
            path = _local_path(name)
            current = _mtime(path)
            if _mtimes.get(path) == current and name in _snapshots:
                continue
            merged, error = _merged_or_error(name)
            if not _validate(name, merged):
                logger.warning("config_hot: %s for %s; keeping previous snapshot",
                               error or "validation failed", name)
                _mtimes[path] = current  # do not re-log the same broken file every tick
                continue
            _snapshots[name] = merged
            _mtimes[path] = current
            swapped.append(name)
    return swapped


def _watch_loop(interval: float) -> None:
    while not _stop.wait(interval):
        try:
            refresh()
        except Exception as exc:  # a broken cycle must never crash the service
            logger.warning("config_hot: watcher error %s", type(exc).__name__)


def start_watcher(interval: float = 2.0) -> None:
    """Install snapshots and start the background mtime poller (idempotent)."""
    global _watcher
    install()
    if _watcher is not None and _watcher.is_alive():
        return
    _stop.clear()
    _watcher = threading.Thread(target=_watch_loop, args=(max(0.2, float(interval)),),
                                name="config_hot", daemon=True)
    _watcher.start()


def stop_watcher() -> None:
    global _watcher
    _stop.set()
    if _watcher is not None:
        _watcher.join(timeout=3.0)
        _watcher = None


__all__ = ["MANAGED", "compute", "install", "refresh", "snapshot", "start_watcher", "stop_watcher"]
