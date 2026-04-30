from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .events import get_event_bus, NODE_STATE_CHANGED
from .node import get_all_nodes, get_downstream

VALID_TRANSITIONS = {
    "pending": {"running"},
    "running": {"done", "failed"},
    "done": {"stale"},
    "stale": {"running"},
    "failed": {"running"},
}


class Blackboard:
    def __init__(self, project_dir: Path) -> None:
        self.project_dir = project_dir
        self.project_dir.mkdir(parents=True, exist_ok=True)
        self._path = project_dir / "blackboard.json"

    def load(self) -> dict[str, Any]:
        if not self._path.exists():
            return {}
        return json.loads(self._path.read_text(encoding="utf-8"))

    def save(self, data: dict[str, Any]) -> None:
        self._path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def init_from_dag(self, user_brief: str = "") -> dict[str, Any]:
        nodes = get_all_nodes()
        data: dict[str, Any] = {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "user_brief": user_brief,
            "nodes": {},
        }
        for name in nodes:
            data["nodes"][name] = {
                "status": "pending",
                "output": None,
                "error": None,
                "updated_at": None,
            }
        self.save(data)
        return data

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _transition(self, node_name: str, new_status: str, **fields: Any) -> dict:
        data = self.load()
        node_data = data["nodes"].get(node_name)
        if node_data is None:
            raise KeyError(f"Unknown node: {node_name}")

        current = node_data["status"]
        if new_status not in VALID_TRANSITIONS.get(current, set()):
            raise ValueError(
                f"Invalid transition for {node_name}: {current} -> {new_status}"
            )

        node_data["status"] = new_status
        node_data["updated_at"] = self._now()
        node_data.update(fields)
        self.save(data)
        return data

    def get_status(self, node_name: str) -> str:
        data = self.load()
        return data["nodes"][node_name]["status"]

    def get_output(self, node_name: str) -> Any:
        data = self.load()
        return data["nodes"][node_name]["output"]

    async def set_running(self, node_name: str) -> None:
        self._transition(node_name, "running", error=None)
        await get_event_bus().emit(
            NODE_STATE_CHANGED,
            {"node": node_name, "status": "running"},
        )

    async def set_done(self, node_name: str, output: Any) -> None:
        self._transition(node_name, "done", output=output, error=None)
        await get_event_bus().emit(
            NODE_STATE_CHANGED,
            {"node": node_name, "status": "done"},
        )

    async def set_failed(self, node_name: str, error: str) -> None:
        self._transition(node_name, "failed", error=error)
        await get_event_bus().emit(
            NODE_STATE_CHANGED,
            {"node": node_name, "status": "failed", "error": error},
        )

    async def invalidate_downstream(self, node_name: str) -> list[str]:
        downstream = get_downstream(node_name)
        data = self.load()
        invalidated = []
        for name in downstream:
            node_data = data["nodes"].get(name)
            if node_data and node_data["status"] == "done":
                node_data["status"] = "stale"
                node_data["updated_at"] = self._now()
                invalidated.append(name)
        self.save(data)
        for name in invalidated:
            await get_event_bus().emit(
                NODE_STATE_CHANGED,
                {"node": name, "status": "stale"},
            )
        return invalidated

    def get_ready_nodes(self) -> list[str]:
        data = self.load()
        all_nodes = get_all_nodes()
        ready = []
        for name, node_def in all_nodes.items():
            status = data["nodes"][name]["status"]
            if status not in ("pending", "stale"):
                continue
            deps_met = all(
                data["nodes"][dep]["status"] == "done"
                for dep in node_def.depends_on
            )
            if deps_met:
                ready.append(name)
        return ready

    def to_summary(self) -> str:
        data = self.load()
        all_nodes = get_all_nodes()
        lines = [f"项目简报: {data.get('user_brief', '(无)')}"]
        lines.append("")
        status_icons = {
            "pending": "⏳",
            "running": "⚙️",
            "done": "✅",
            "failed": "❌",
            "stale": "🔄",
        }
        for name in all_nodes:
            nd = data["nodes"][name]
            icon = status_icons.get(nd["status"], "?")
            deps = all_nodes[name].depends_on
            dep_str = f" (依赖: {', '.join(deps)})" if deps else ""
            line = f"  {icon} {name}: {nd['status']}{dep_str}"
            if nd["status"] == "failed" and nd.get("error"):
                line += f" — {nd['error'][:80]}"
            lines.append(line)
        ready = self.get_ready_nodes()
        if ready:
            lines.append(f"\n可执行节点: {', '.join(ready)}")
        return "\n".join(lines)
