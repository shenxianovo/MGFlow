from __future__ import annotations

import importlib
import pkgutil
from abc import ABC, abstractmethod
from typing import Any

_TOOL_REGISTRY: dict[str, type[ToolBase]] = {}


class ToolBase(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, **kwargs: Any) -> dict:
        ...

    def to_function_schema(self) -> dict:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def tool(name: str):
    def decorator(cls: type[ToolBase]) -> type[ToolBase]:
        cls.name = name
        _TOOL_REGISTRY[name] = cls
        return cls
    return decorator


def get_tool(name: str) -> ToolBase:
    cls = _TOOL_REGISTRY.get(name)
    if cls is None:
        raise KeyError(f"Unknown tool: {name}")
    return cls()


def get_all_tools() -> dict[str, type[ToolBase]]:
    return dict(_TOOL_REGISTRY)


def discover_tools(package_name: str = "tools") -> None:
    package = importlib.import_module(package_name)
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module_name}")
