"""DynamoDB access.

In M1 this exists only to prove connectivity from ``/health/deps``; M3 grows it
into the processing-job store.

boto3 is synchronous. Calling it directly from an async route would block the
event loop, so every call is dispatched to a worker thread. An async AWS client
(aioboto3) was considered and rejected: it adds a dependency for what is, in
this project, a handful of small calls per request.
"""

from __future__ import annotations

from functools import lru_cache
from typing import TYPE_CHECKING, Any

from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from app.core.aws_clients import build_client
from app.core.config import settings
from app.core.logging import get_logger

if TYPE_CHECKING:
    from app.services.job_store import JobStore

logger = get_logger(__name__)


@lru_cache
def get_dynamodb_client() -> Any:
    """Cached boto3 client.

    Client construction parses config files and is comparatively slow, so it is
    done once per process. Clients are thread-safe for this usage.
    """
    # build_client enforces STORAGE_BACKEND (ADR 0010): locally this can only
    # ever reach DynamoDB Local.
    return build_client(
        "dynamodb",
        backend=settings.storage_backend,
        endpoint_url=settings.dynamodb_endpoint_url,
        region=settings.aws_region,
        access_key=settings.aws_access_key_id,
        secret_key=settings.aws_secret_access_key,
        setting_name="DYNAMODB_ENDPOINT_URL",
        config=Config(
            retries={"max_attempts": 3, "mode": "standard"},
            connect_timeout=3,
            read_timeout=5,
        ),
    )


async def check_health(store: JobStore) -> tuple[bool, str]:
    """Return (healthy, detail). Never raises.

    Checks the jobs table specifically, not just connectivity: a reachable
    DynamoDB without the table cannot accept a single processing job.
    """
    try:
        status = await store.table_status()
        return status == "ACTIVE", f"reachable (jobs table {status})"
    except (ClientError, BotoCoreError) as exc:
        logger.warning("dynamodb health check failed", extra={"error": str(exc)})
        return False, type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - health checks must never propagate
        logger.warning("dynamodb health check failed", extra={"error": str(exc)})
        return False, type(exc).__name__
