"""Pytest fixtures.

Isolation strategy
------------------
PostgreSQL: tests run against a dedicated database (``minuteai_test``), created
by the Postgres container's init script. Schema is applied once per session by
running the real Alembic migrations - so the migrations themselves are
exercised on every test run, rather than being assumed correct.

Each test then runs inside a transaction that is rolled back afterwards.
``join_transaction_mode="create_savepoint"`` means a ``session.commit()`` inside
application code creates a SAVEPOINT instead of committing the outer
transaction, so route handlers behave normally while the database still ends
the test exactly as it started.

DynamoDB: a uniquely named table on DynamoDB Local is created for the session
and emptied after every test. Job-store tests therefore exercise real
conditional writes, transactions, and index queries rather than a mock.

Background work: the worker is never started implicitly (ASGITransport does not
run the app's lifespan). Tests call ``worker.run_once()`` exactly when they want
queued jobs to execute, which keeps them deterministic.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncGenerator, AsyncIterator, Generator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

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
from app.services.dynamo import get_dynamodb_client
from app.services.embeddings import get_embedder
from app.services.job_store import JobStore, get_job_store
from app.services.llm.factory import get_llm_provider, get_transcription_provider
from app.services.storage import ObjectStorage, _client, get_storage
from app.workers.processing import ProcessingWorker
from tests.fakes import FakeEmbedder, FakeLLMProvider, FakeTranscriber

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(scope="session", autouse=True)
def enable_app_logging(apply_migrations) -> None:
    """Run every log call for real.

    With the default WARNING level, ``logger.info(..., extra=...)`` returns
    before building a LogRecord, so a bad ``extra`` key (e.g. the reserved
    ``created``) raises only in production, where the level is INFO. That bug
    shipped once; at DEBUG the tests build every record and would catch it.
    """
    import logging

    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.DEBUG)
    # Guard against anything (e.g. a logging.config call) having disabled it.
    assert not app_logger.disabled


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


# ---------------------------------------------------------------------------
# DynamoDB job store
# ---------------------------------------------------------------------------


class FakeClock:
    """Controllable time for lease expiry and retry back-off tests."""

    def __init__(self) -> None:
        self.now = datetime.now(UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture(scope="session")
def jobs_table_name() -> Generator[str, None, None]:
    client = get_dynamodb_client()
    name = f"minuteai_jobs_test_{uuid.uuid4().hex[:8]}"
    store = JobStore(client=client, table_name=name, max_attempts=3, ttl_days=1)

    import asyncio

    asyncio.run(store.ensure_table())
    yield name
    client.delete_table(TableName=name)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def job_store(jobs_table_name: str, clock: FakeClock) -> Generator[JobStore, None, None]:
    client = get_dynamodb_client()
    yield JobStore(
        client=client, table_name=jobs_table_name, max_attempts=3, ttl_days=1, clock=clock
    )
    # Empty the table so jobs and meeting locks never leak between tests.
    paginator = client.get_paginator("scan")
    for page in paginator.paginate(TableName=jobs_table_name, ProjectionExpression="pk"):
        for item in page.get("Items", []):
            client.delete_item(TableName=jobs_table_name, Key={"pk": item["pk"]})


# ---------------------------------------------------------------------------
# S3-compatible object storage
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def storage_bucket() -> Generator[str, None, None]:
    """A uniquely named bucket on the local object-storage container, per session."""
    import asyncio

    client = _client(settings.s3_endpoint_url)
    name = f"minuteai-test-{uuid.uuid4().hex[:8]}"
    storage = ObjectStorage(client=client, signing_client=client, bucket=name)
    asyncio.run(storage.ensure_bucket(cors_origins=["http://localhost:5173"]))
    yield name
    asyncio.run(storage.delete_prefix(""))
    client.delete_bucket(Bucket=name)


@pytest.fixture
def storage(storage_bucket: str) -> Generator[ObjectStorage, None, None]:
    client = _client(settings.s3_endpoint_url)
    yield ObjectStorage(client=client, signing_client=client, bucket=storage_bucket)
    # Empty the bucket so objects never leak between tests.
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=storage_bucket):
        keys = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if keys:
            client.delete_objects(Bucket=storage_bucket, Delete={"Objects": keys, "Quiet": True})


@pytest.fixture
def fake_transcriber() -> FakeTranscriber:
    return FakeTranscriber()


@pytest.fixture
def fake_embedder() -> FakeEmbedder:
    return FakeEmbedder()


@pytest.fixture
def fake_llm() -> FakeLLMProvider:
    """The LLM seen by the app in tests. Tests may reconfigure it before calling."""
    return FakeLLMProvider()


@pytest.fixture
def worker(
    db_session: AsyncSession,
    job_store: JobStore,
    fake_llm: FakeLLMProvider,
    storage: ObjectStorage,
    fake_transcriber: FakeTranscriber,
    fake_embedder: FakeEmbedder,
) -> ProcessingWorker:
    @asynccontextmanager
    async def shared_session() -> AsyncIterator[AsyncSession]:
        # The worker normally opens its own session per job. In tests it reuses
        # the test session so its writes are visible to, and rolled back with,
        # the rest of the test.
        yield db_session

    return ProcessingWorker(
        store=job_store,
        session_factory=shared_session,
        llm_factory=lambda: fake_llm,
        storage=storage,
        transcriber_factory=lambda: fake_transcriber,
        embedder_factory=lambda: fake_embedder,
        lease_seconds=60,
        retry_base_seconds=30,
        worker_id="test-worker",
    )


@pytest.fixture
async def client(
    db_session: AsyncSession,
    fake_llm: FakeLLMProvider,
    job_store: JobStore,
    storage: ObjectStorage,
    fake_transcriber: FakeTranscriber,
    fake_embedder: FakeEmbedder,
) -> AsyncGenerator[AsyncClient, None]:
    """HTTP client wired to the app: test DB session, fake LLM, test job table."""

    async def _override_get_db() -> AsyncGenerator[AsyncSession, None]:
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[get_llm_provider] = lambda: fake_llm
    app.dependency_overrides[get_job_store] = lambda: job_store
    app.dependency_overrides[get_storage] = lambda: storage
    app.dependency_overrides[get_transcription_provider] = lambda: fake_transcriber
    app.dependency_overrides[get_embedder] = lambda: fake_embedder
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


@pytest.fixture
def process_and_wait(client: AsyncClient, worker: ProcessingWorker):
    """Submit processing, let the worker run it, return the stored intelligence.

    Asserts the submission was accepted (202) or served from cache (200).
    """

    async def _run(meeting_id: str, headers: dict[str, str], force: bool = False) -> dict:
        url = f"/api/v1/meetings/{meeting_id}/process" + ("?force=true" if force else "")
        submitted = await client.post(url, headers=headers)
        assert submitted.status_code in (200, 202), submitted.text
        await worker.run_once()
        intelligence = await client.get(
            f"/api/v1/meetings/{meeting_id}/intelligence", headers=headers
        )
        assert intelligence.status_code == 200, intelligence.text
        return {"submission": submitted.json(), **intelligence.json()}

    return _run
