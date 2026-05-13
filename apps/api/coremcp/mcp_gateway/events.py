from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class GatewayEvent:
    id: int
    event: str
    data: dict[str, Any]


class EventSubscription:
    def __init__(self, bus: "ListChangedEventBus", queue: asyncio.Queue[GatewayEvent]) -> None:
        self._bus = bus
        self._queue = queue
        self._closed = False

    async def get(self) -> GatewayEvent:
        return await self._queue.get()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        await self._bus.unsubscribe(self._queue)


class ListChangedEventBus:
    """Process-local event bus for MCP catalog change notifications.

    P1 runs as a single personal gateway process, so an in-memory fan-out queue is
    sufficient and avoids persisting notification payloads.
    """

    def __init__(self, *, max_queue_size: int = 32) -> None:
        self._max_queue_size = max_queue_size
        self._subscribers: set[asyncio.Queue[GatewayEvent]] = set()
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def subscribe(self) -> EventSubscription:
        queue: asyncio.Queue[GatewayEvent] = asyncio.Queue(maxsize=self._max_queue_size)
        async with self._lock:
            self._subscribers.add(queue)
        return EventSubscription(self, queue)

    async def unsubscribe(self, queue: asyncio.Queue[GatewayEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish_list_changed(self, *, reason: str, resource_id: str | None = None) -> GatewayEvent:
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": "notifications/tools/list_changed",
            "params": {},
        }
        metadata: dict[str, Any] = {"reason": reason}
        if resource_id:
            metadata["resource_id"] = resource_id
        data["_meta"] = {"coremcp": metadata}

        async with self._lock:
            self._next_id += 1
            event = GatewayEvent(id=self._next_id, event="listChanged", data=data)
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event
