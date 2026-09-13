"""Pytest fixtures.

Isolation strategy
------------------
Tests run against a dedicated database (``minuteai_test``), created by the
Postgres container's init script. Schema is applied once per session by running
the real Alembic migrations - so the migrations themselves are exercised on
every test run, rather than being assumed correct.

Each test then runs inside a transaction that is rolled back afterwards.
``join_transaction_mode="create_savepoint"`` means a ``session.commit()`` inside
application code creates a SAVEPOINT instead of committing the outer
transaction, so route handlers behave normally while the database still ends
the test exactly as it started.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, Generator
from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.core.config import settings
from app.core.security import hash_password
from app.db.models.user import User
from app.db.session import get_db
from app.main import app
from app.services.llm.factory import get_llm_provider
from tests.fakes import FakeLLMProvider

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def apply_migrations() -> Generator[None, None, None]:
    """Bring the test database to head using the project's own migrations."""
    os.environ["ALEMBIC_DATABASE_URL"] = settings.test_database_url

    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "app", "db", "migrations"))

    # Start from a known-empty schema so a previous failed run cannot leak state.
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine(settings.test_database_url, poolclass=None)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(
            bind=connection,
            expire_on_commit=False,
            join_transaction_mode="create_savepoint",
        )
        try:
            yield session
        finally:
            await session.close()
            if transaction.is_active:
                await transaction.rollback()
    await engine.dispose()


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    """The LLM seen by the app in tests. Tests may reconfigure it before calling."""
    return FakeLLMProvider()


@pytest.fixture
async def client(
    db_session: AsyncSession, fake_llm: FakeLLMProvider
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired to the app: test DB session and fake LLM injected."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------

DEFAULT_PASSWORD = "correct-horse-battery"


@pytest.fixture
def make_user(db_session: AsyncSession):
    """Factory creating a persisted user with a known password."""

    async def _make(email: str | None = None, password: str = DEFAULT_PASSWORD) -> User:
        user = User(
            email=email or f"user-{uuid.uuid4().hex[:8]}@example.com",
            password_hash=hash_password(password),
            full_name="Test User",
        )
        db_session.add(user)
        await db_session.commit()
        await db_session.refresh(user)
        return user

    return _make


@pytest.fixture
def auth_headers(client: AsyncClient):
    """Log a user in and return the Authorization header for them."""

    async def _headers(user: User, password: str = DEFAULT_PASSWORD) -> dict[str, str]:
        response = await client.post(
            "/api/v1/auth/login",
            data={"username": user.email, "password": password},
        )
        assert response.status_code == 200, response.text
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    return _headers


@pytest.fixture
def meeting_payload():
    def _payload(**overrides) -> dict:
        base = {
            "title": "Sprint planning",
            "meeting_date": datetime(2026, 9, 10, 10, 0, tzinfo=UTC).isoformat(),
            "description": "Planning the next sprint",
            "source_type": "text",
        }
        base.update(overrides)
        return base

    return _payload


@pytest.fixture
def create_meeting(client: AsyncClient, meeting_payload):
    async def _create(headers: dict[str, str], **overrides) -> dict:
        response = await client.post(
            "/api/v1/meetings", json=meeting_payload(**overrides), headers=headers
        )
        assert response.status_code == 201, response.text
        return response.json()

    return _create


@pytest.fixture
def put_transcript(client: AsyncClient):
    async def _put(meeting_id: str, headers: dict[str, str], content: str) -> dict:
        response = await client.put(
            f"/api/v1/meetings/{meeting_id}/transcript",
            json={"content": content, "language": "en"},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        return response.json()

    return _put
