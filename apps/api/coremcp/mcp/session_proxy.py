from __future__ import annotations

import time
from typing import Awaitable, Callable

from fastapi import FastAPI

DEFAULT_DOWNSTREAM_SESSION_KEY = "__default__"


def downstream_session_key(service_id: str | None) -> str:
    return str(service_id or DEFAULT_DOWNSTREAM_SESSION_KEY)


def downstream_session_id(app: FastAPI, service_id: str | None) -> str | None:
    sessions = getattr(app.state, "downstream_sessions", {})
    key = downstream_session_key(service_id)
    entry = sessions.get(key)
    if isinstance(entry, str):
        return entry if entry else None
    if not isinstance(entry, dict):
        return None
    expires_at = entry.get("expires_at")
    if isinstance(expires_at, int | float) and expires_at <= time.time():
        sessions.pop(key, None)
        return None
    session_id = entry.get("session_id")
    return session_id if isinstance(session_id, str) and session_id else None


def set_downstream_session(app: FastAPI, service_id: str | None, session_id: str) -> None:
    cleaned = session_id.strip()
    if not cleaned:
        return
    now = time.time()
    ttl_seconds = max(1, int(app.state.settings.downstream_session_ttl_seconds))
    app.state.downstream_sessions[downstream_session_key(service_id)] = {
        "session_id": cleaned,
        "updated_at": now,
        "expires_at": now + ttl_seconds,
    }


def downstream_session_callback(app: FastAPI, service_id: str | None) -> Callable[[str], Awaitable[None]]:
    async def callback(session_id: str) -> None:
        set_downstream_session(app, service_id, session_id)

    return callback


def forget_downstream_session(app: FastAPI, service_id: str | None) -> None:
    sessions = getattr(app.state, "downstream_sessions", None)
    if isinstance(sessions, dict):
        sessions.pop(downstream_session_key(service_id), None)


def reap_expired_downstream_sessions(app: FastAPI) -> int:
    sessions = getattr(app.state, "downstream_sessions", None)
    if not isinstance(sessions, dict):
        return 0
    now = time.time()
    removed = 0
    for key, entry in list(sessions.items()):
        if isinstance(entry, dict) and isinstance(entry.get("expires_at"), int | float) and entry["expires_at"] <= now:
            sessions.pop(key, None)
            removed += 1
    return removed
