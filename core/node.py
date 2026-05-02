from __future__ import annotations

import importlib
import pkgutil
from collections import deque
from dataclasses import dataclass, field
from typing import Any

_NODE_REGISTRY: dict[str, NodeDef] = {}


@dataclass
class NodeDef:
    name: str
    depends_on: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    system_prompt: str = ""
    max_iterations: int = 10
    max_tokens: int = 16000
    deterministic: bool = False


class Node:
    """Base class for node definitions. Subclasses set class-level attributes."""
    system_prompt: str = ""


def node(
    name: str,
    depends_on: list[str] | None = None,
    tools: list[str] | None = None,
    max_iterations: int = 10,
    max_tokens: int = 16000,
    deterministic: bool = False,
):
    def decorator(cls: type[Node]) -> type[Node]:
        node_def = NodeDef(
            name=name,
            depends_on=depends_on or [],
            tools=tools or [],
            system_prompt=cls.system_prompt,
            max_iterations=max_iterations,
            max_tokens=max_tokens,
            deterministic=deterministic,
        )
        _NODE_REGISTRY[name] = node_def
        cls._node_def = node_def
        return cls
    return decorator


def get_node(name: str) -> NodeDef:
    node_def = _NODE_REGISTRY.get(name)
    if node_def is None:
        raise KeyError(f"Unknown node: {name}")
    return node_def


def get_all_nodes() -> dict[str, NodeDef]:
    return dict(_NODE_REGISTRY)


def get_dag() -> dict[str, list[str]]:
    """Returns adjacency list: node -> list of nodes that depend on it."""
    dag: dict[str, list[str]] = {name: [] for name in _NODE_REGISTRY}
    for name, node_def in _NODE_REGISTRY.items():
        for dep in node_def.depends_on:
            if dep in dag:
                dag[dep].append(name)
    return dag


def get_downstream(node_name: str) -> set[str]:
    """BFS to find all transitive dependents of a node."""
    dag = get_dag()
    visited: set[str] = set()
    queue = deque(dag.get(node_name, []))
    while queue:
        current = queue.popleft()
        if current in visited:
            continue
        visited.add(current)
        queue.extend(dag.get(current, []))
    return visited


def discover_nodes(package_name: str = "nodes") -> None:
    package = importlib.import_module(package_name)
    for _, module_name, _ in pkgutil.iter_modules(package.__path__):
        importlib.import_module(f"{package_name}.{module_name}")
