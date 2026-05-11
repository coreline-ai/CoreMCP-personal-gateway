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
        session.updated_at = datetime.now(UTC)

    def delete(self, session_id: str | None) -> bool:
        if not session_id:
            return False
        return self._sessions.pop(session_id, None) is not None
