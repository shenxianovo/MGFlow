from __future__ import annotations

import asyncio
from collections import defaultdict
from typing import Any, Callable, Coroutine

WORKER_STARTED = "worker_started"
WORKER_COMPLETED = "worker_completed"
WORKER_FAILED = "worker_failed"
WORKER_NEED_INPUT = "worker_need_input"
WORKER_PROGRESS = "worker_progress"
WORKER_TOKEN = "worker_token"
NODE_STATE_CHANGED = "node_state_changed"
ORCHESTRATOR_MESSAGE = "orchestrator_message"


class EventBus:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[..., Coroutine]]] = defaultdict(list)

    def subscribe(self, event_type: str, callback: Callable[..., Coroutine]) -> None:
        self._subscribers[event_type].append(callback)

    def unsubscribe(self, event_type: str, callback: Callable[..., Coroutine]) -> None:
        self._subscribers[event_type] = [
            cb for cb in self._subscribers[event_type] if cb is not callback
        ]

    async def emit(self, event_type: str, data: dict[str, Any] | None = None) -> None:
        data = data or {}
        data["event_type"] = event_type
        for callback in self._subscribers.get(event_type, []):
            try:
                asyncio.create_task(callback(data))
            except RuntimeError:
                await callback(data)


_bus: EventBus | None = None


def get_event_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus()
    return _bus
