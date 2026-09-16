"""Async database engine and session management."""

from __future__ import annotations

from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings

engine = create_async_engine(
    settings.database_url,
    echo=False,
    # Validates a pooled connection before handing it out. Without it, a
    # connection dropped by the server (container restart, managed-database failover)
    # surfaces as a random request failure instead of being replaced silently.
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    # Keeps attributes readable after commit(). With the default (True), every
    # attribute access after a commit triggers a refresh - which in async
    # SQLAlchemy raises MissingGreenlet if it happens outside the session
    # context, e.g. while FastAPI serialises the response model.
    expire_on_commit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency yielding a request-scoped session.

    The session is closed when the request ends. Routes commit explicitly;
    anything that escapes as an exception is rolled back here.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
