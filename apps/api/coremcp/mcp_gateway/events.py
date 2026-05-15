from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any, Literal

ListChangedCategory = Literal["tools", "resources", "prompts"]
LIST_CHANGED_CATEGORIES: tuple[ListChangedCategory, ...] = ("tools", "resources", "prompts")


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
    sufficient and avoids persisting notification payloads. The replay buffer is
    intentionally short-lived and exists only to support EventSource
    ``Last-Event-Id`` reconnect backfill for recent list_changed invalidations.
    """

    def __init__(self, *, max_queue_size: int = 32, replay_size: int = 64) -> None:
        self._max_queue_size = max_queue_size
        self._replay_size = max(1, replay_size)
        self._subscribers: set[asyncio.Queue[GatewayEvent]] = set()
        self._events: deque[GatewayEvent] = deque(maxlen=self._replay_size)
        self._next_id = 0
        self._lock = asyncio.Lock()

    async def subscribe(self, *, last_event_id: int | None = None) -> EventSubscription:
        queue: asyncio.Queue[GatewayEvent] = asyncio.Queue(
            maxsize=max(self._max_queue_size, self._replay_size)
        )
        async with self._lock:
            if last_event_id is not None:
                for event in self._events:
                    if event.id > last_event_id:
                        queue.put_nowait(event)
            self._subscribers.add(queue)
        return EventSubscription(self, queue)

    async def unsubscribe(self, queue: asyncio.Queue[GatewayEvent]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish_list_changed(
        self,
        *,
        reason: str,
        category: ListChangedCategory = "tools",
        resource_id: str | None = None,
    ) -> GatewayEvent:
        if category not in LIST_CHANGED_CATEGORIES:
            raise ValueError(f"unsupported list_changed category: {category}")
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": f"notifications/{category}/list_changed",
            "params": {},
        }
        metadata: dict[str, Any] = {"reason": reason, "category": category}
        if resource_id:
            metadata["resource_id"] = resource_id
        data["_meta"] = {"coremcp": metadata}

        async with self._lock:
            self._next_id += 1
            event = GatewayEvent(id=self._next_id, event="listChanged", data=data)
            self._events.append(event)
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                # list_changed is a coalescing invalidation signal. A slow SSE
                # consumer only needs the newest "catalog changed" notice, so
                # drop the oldest queued event instead of blocking publishers.
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event

    async def publish_notification(
        self,
        *,
        method: str,
        params: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        event_name: str = "notification",
    ) -> GatewayEvent:
        data: dict[str, Any] = {
            "jsonrpc": "2.0",
            "method": method,
            "params": params or {},
        }
        if metadata:
            data["_meta"] = {"coremcp": metadata}

        async with self._lock:
            self._next_id += 1
            event = GatewayEvent(id=self._next_id, event=event_name, data=data)
            self._events.append(event)
            subscribers = list(self._subscribers)

        for queue in subscribers:
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)
        return event
