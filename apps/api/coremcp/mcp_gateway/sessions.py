from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import uuid4


@dataclass(slots=True)
class McpSession:
    id: str
    protocol_version: str
    initialized: bool = False
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    last_seen: datetime = field(default_factory=lambda: datetime.now(UTC))


class SessionStore:
    """Single-process in-memory MCP session store for P0/P1."""

    def __init__(self) -> None:
        self._sessions: dict[str, McpSession] = {}

    def create(self, protocol_version: str) -> McpSession:
        session = McpSession(id=str(uuid4()), protocol_version=protocol_version)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: str | None) -> McpSession | None:
        if not session_id:
            return None
        return self._sessions.get(session_id)

    def mark_initialized(self, session_id: str | None) -> None:
        session = self.get(session_id)
        if session is None:
            return
        session.initialized = True
        self.touch(session_id)

    def touch(self, session_id: str | None, now: datetime | None = None) -> bool:
        session = self.get(session_id)
        if session is None:
            return False
        current = now or datetime.now(UTC)
        session.last_seen = current
        session.updated_at = current
        return True

    def reap_idle(self, max_idle_seconds: float, now: datetime | None = None) -> int:
        """Delete sessions idle for at least ``max_idle_seconds`` and return count.

        TODO: Wire this pure in-memory helper into the gateway operations loop
        owned by main.py once the scheduler/inflight job reaper contract is added.
        """
        if max_idle_seconds < 0:
            raise ValueError("max_idle_seconds must be >= 0")
        current = now or datetime.now(UTC)
        expired_ids = [
            session_id
            for session_id, session in self._sessions.items()
            if (current - session.last_seen).total_seconds() >= max_idle_seconds
        ]
        for session_id in expired_ids:
            self._sessions.pop(session_id, None)
        return len(expired_ids)

    def delete(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        return self._sessions.pop(session_id, None) is not None

    def count_active(self) -> int:
        return len(self._sessions)
