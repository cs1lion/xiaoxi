"""适配器注册表：类型字符串 -> 适配器类。

两种登记方式等价：

    @register_adapter("cli_subproc")
    class CliSubprocessAdapter(BaseWorkerAdapter): ...

    register_adapter("http_api", HttpApiAdapter)

调度核心只做一件事：`create_adapter_for(花名册条目)`。它读条目里的 `adapter`
字符串去查表，查不到就抛 UnknownAdapterError——由派单方决定是换将还是请示老板，
注册表自己不猜。
"""
from __future__ import annotations

import inspect
from typing import Any, Callable, TypeVar

from .base import BaseWorkerAdapter, UnknownAdapterError

_REGISTRY: dict[str, type[BaseWorkerAdapter]] = {}
_T = TypeVar("_T", bound=BaseWorkerAdapter)


def register_adapter(type_name: str, cls: type[BaseWorkerAdapter] | None = None):
    """登记一个适配器类。可作装饰器，也可直接传类调用。"""
    if not isinstance(type_name, str) or not type_name.strip():
        raise ValueError("adapter type name must be nonempty text")
    name = type_name.strip()

    def install(candidate: type[BaseWorkerAdapter]) -> type[BaseWorkerAdapter]:
        if not isinstance(candidate, type) or not issubclass(candidate, BaseWorkerAdapter):
            raise ValueError("adapter must be a BaseWorkerAdapter subclass")
        if inspect.isabstract(candidate):
            # 少了 prepare_context / execute / cancel 任何一个都算没实现完。
            # 放它进去只会在派单时才炸，那时候老板已经在等了。
            missing = sorted(getattr(candidate, "__abstractmethods__", ()))
            raise ValueError(f"adapter {candidate.__name__} 未实现：{', '.join(missing)}")
        if not candidate.adapter_type:
            candidate.adapter_type = name
        elif candidate.adapter_type != name:
            raise ValueError(
                f"adapter class declares type {candidate.adapter_type!r} but is registered as {name!r}")
        _REGISTRY[name] = candidate
        return candidate

    return install if cls is None else install(cls)


def adapter_types() -> list[str]:
    """已登记的类型名，按登记顺序（观测与自检用）。"""
    return list(_REGISTRY)


def get_adapter_class(type_name: str) -> type[BaseWorkerAdapter]:
    """取类；未登记抛 UnknownAdapterError。"""
    if not isinstance(type_name, str) or not type_name.strip():
        raise UnknownAdapterError("adapter type name must be nonempty text")
    cls = _REGISTRY.get(type_name.strip())
    if cls is None:
        raise UnknownAdapterError(f"adapter type {type_name!r} is not registered")
    return cls


def create_adapter(type_name: str, config: dict[str, Any] | None = None) -> BaseWorkerAdapter:
    """按类型名实例化。配置构造失败一律包成 UnknownAdapterError，方便上层统一处理。"""
    cls = get_adapter_class(type_name)
    try:
        return cls(config)
    except UnknownAdapterError:
        raise
    except Exception as exc:
        raise UnknownAdapterError(f"adapter {type_name!r} failed to initialise: {exc}") from exc


def create_adapter_for(agent: dict[str, Any]) -> BaseWorkerAdapter:
    """从花名册条目构造适配器——调度核心唯一需要调用的入口。

    `adapter` 缺省视为 `manual`（只组密件、不执行），这样老花名册不必改写也能跑。
    """
    if not isinstance(agent, dict):
        raise UnknownAdapterError("agent entry must be an object")
    type_name = agent.get("adapter") or "manual"
    return create_adapter(str(type_name), agent)


def unregister_adapter(type_name: str) -> bool:
    """测试用：摘掉一个登记。"""
    return _REGISTRY.pop(str(type_name).strip(), None) is not None


def reset_registry() -> None:
    """测试用：清空登记表。"""
    _REGISTRY.clear()


__all__ = ["adapter_types", "create_adapter", "create_adapter_for", "get_adapter_class",
           "register_adapter", "reset_registry", "unregister_adapter"]
