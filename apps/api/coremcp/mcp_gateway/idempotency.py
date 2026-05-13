from __future__ import annotations

import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any


class IdempotencyCache:
    """Small in-memory TTL cache for repeated tools/call requests.

    This is intentionally process-local and non-persistent to avoid writing raw
    tool outputs to DB. It only covers duplicate client retries within a short
    window in the single-process personal gateway.
    """

    def __init__(self, *, ttl_seconds: int = 600, max_entries: int = 128) -> None:
        self.ttl_seconds = ttl_seconds
        self.max_entries = max_entries
        self._items: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()

    def get(self, key: str | None) -> dict[str, Any] | None:
        if not key:
            return None
        self._purge()
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.time():
            self._items.pop(key, None)
            return None
        self._items.move_to_end(key)
        return deepcopy(value)

    def set(self, key: str | None, value: dict[str, Any]) -> None:
        if not key:
            return
        self._purge()
        self._items[key] = (time.time() + self.ttl_seconds, deepcopy(value))
        self._items.move_to_end(key)
        while len(self._items) > self.max_entries:
            self._items.popitem(last=False)

    def _purge(self) -> None:
        now = time.time()
        expired = [key for key, (expires_at, _) in self._items.items() if expires_at <= now]
        for key in expired:
            self._items.pop(key, None)
