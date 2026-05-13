from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from coremcp.settings import Settings, get_settings


def sqlite_async_url(database_path: Path) -> str:
    if str(database_path) == ":memory:":
        return "sqlite+aiosqlite:///:memory:"
    return f"sqlite+aiosqlite:///{database_path.expanduser()}"


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    database_path = settings.resolved_database_path
    if str(database_path) != ":memory:":
        database_path.parent.mkdir(parents=True, exist_ok=True)
    return create_async_engine(sqlite_async_url(database_path), future=True)


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)


async def session_scope(settings: Settings | None = None) -> AsyncIterator[AsyncSession]:
    engine = create_engine(settings)
    session_factory = create_session_factory(engine)
    try:
        async with session_factory() as session:
            yield session
    finally:
        await engine.dispose()
