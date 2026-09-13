"""Processing-job state in DynamoDB (ADR 0008).

Table layout: one table, two item types, two sparse GSIs.

    Job item    pk = JOB#<job_id>
                meeting_pk = MEETING#<meeting_id>  ─┐ gsi_meeting (history per meeting)
                created_at                          ─┘
                job_status                          ─┐ gsi_status (work queue + lease scan)
                available_at                        ─┘
                attempts, max_attempts, force, worker_id, events[], job_result{}, error_*
                expires_at  (TTL, epoch seconds)

    Lock item   pk = LOCK#MEETING#<meeting_id>, job_id
                exists exactly while a job for that meeting is QUEUED or PROCESSING

Access patterns and how each is served:

    poll one job                     GetItem(pk) with ConsistentRead
    job history for a meeting        Query gsi_meeting, newest first
    next job to run                  Query gsi_status QUEUED where available_at <= now
    jobs whose worker died           Query gsi_status PROCESSING where available_at < now
    at most one active job/meeting   TransactWrite: put job + put lock IF NOT EXISTS
    only one worker runs a job       UpdateItem ... IF job_status = QUEUED AND available_at = seen

``available_at`` is overloaded on purpose: for a QUEUED job it is when the job
becomes runnable (now, or a retry back-off time); for a PROCESSING job it is when
the worker's lease expires. One index range key therefore answers both "what can
I start?" and "what has been abandoned?".

Why the job id, not the meeting id, is the partition key: the most frequent read
is a client polling one job's status. A base-table GetItem can be strongly
consistent; a GSI query cannot. Per-meeting history is rarer and tolerates the
index's eventual consistency.

boto3 is synchronous, so every call runs in a worker thread (see dynamo.py).
"""

from __future__ import annotations

import enum
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from functools import lru_cache
from typing import Any

from boto3.dynamodb.types import TypeDeserializer, TypeSerializer
from botocore.exceptions import BotoCoreError, ClientError
from starlette.concurrency import run_in_threadpool

from app.core.config import settings
from app.core.exceptions import ServiceUnavailableError
from app.core.logging import get_logger

logger = get_logger(__name__)

GSI_MEETING = "gsi_meeting"
GSI_STATUS = "gsi_status"
_CLAIM_BATCH = 10

# Errors that genuinely mean "DynamoDB cannot serve this right now". Anything
# else (e.g. ValidationException) is a bug in the request and is re-raised
# as-is, so it surfaces as a loud 500 with a traceback instead of being
# disguised as a transient outage.
_UNAVAILABLE_CODES = frozenset(
    {
        "ProvisionedThroughputExceededException",
        "ThrottlingException",
        "RequestLimitExceeded",
        "InternalServerError",
        "ServiceUnavailable",
    }
)

_ser = TypeSerializer()
_deser = TypeDeserializer()


class JobStatus(enum.StrEnum):
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


TERMINAL_STATUSES = frozenset({JobStatus.COMPLETED, JobStatus.FAILED})


class JobStoreUnavailableError(ServiceUnavailableError):
    code = "job_store_unavailable"
    message = "The job store is temporarily unavailable."


def _iso(dt: datetime) -> str:
    # Fixed-width ISO-8601 in UTC, so lexicographic order == chronological
    # order. Range keys (created_at, available_at) depend on that.
    return dt.astimezone(UTC).isoformat(timespec="milliseconds")


def _job_pk(job_id: str) -> str:
    return f"JOB#{job_id}"


def _lock_pk(meeting_id: str) -> str:
    return f"LOCK#MEETING#{meeting_id}"


def _to_ddb(values: dict[str, Any]) -> dict[str, dict]:
    return {k: _ser.serialize(v) for k, v in values.items()}


def _from_ddb(item: dict[str, dict]) -> dict[str, Any]:
    def plain(v: Any) -> Any:
        if isinstance(v, Decimal):
            return int(v) if v == v.to_integral_value() else float(v)
        if isinstance(v, list):
            return [plain(x) for x in v]
        if isinstance(v, dict):
            return {k: plain(x) for k, x in v.items()}
        return v

    return {k: plain(_deser.deserialize(v)) for k, v in item.items()}


@dataclass(slots=True)
class JobRecord:
    job_id: str
    meeting_id: str
    owner_id: str
    status: JobStatus
    force: bool
    attempts: int
    max_attempts: int
    created_at: str
    updated_at: str
    available_at: str
    started_at: str | None = None
    finished_at: str | None = None
    worker_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    result: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_item(cls, item: dict[str, dict]) -> JobRecord:
        d = _from_ddb(item)
        return cls(
            job_id=d["job_id"],
            meeting_id=d["meeting_id"],
            owner_id=d["owner_id"],
            status=JobStatus(d["job_status"]),
            force=bool(d.get("force", False)),
            attempts=int(d.get("attempts", 0)),
            max_attempts=int(d["max_attempts"]),
            created_at=d["created_at"],
            updated_at=d["updated_at"],
            available_at=d["available_at"],
            started_at=d.get("started_at"),
            finished_at=d.get("finished_at"),
            worker_id=d.get("worker_id"),
            error_code=d.get("error_code"),
            error_message=d.get("error_message"),
            result=d.get("job_result"),
            events=d.get("events") or [],
        )

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES


@dataclass(slots=True)
class RecoveredJob:
    job: JobRecord
    requeued: bool  # False means it was failed permanently


class JobStore:
    def __init__(
        self,
        *,
        client: Any,
        table_name: str,
        max_attempts: int,
        ttl_days: int,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._client = client
        self.table_name = table_name
        self._max_attempts = max_attempts
        self._ttl_days = ttl_days
        # Injectable clock: lets tests move time forward to expire a lease
        # instead of sleeping for real.
        self._clock = clock

    # ------------------------------------------------------------------
    # Plumbing
    # ------------------------------------------------------------------

    async def _call(self, operation: str, **kwargs: Any) -> Any:
        method = getattr(self._client, operation)
        try:
            return await run_in_threadpool(lambda: method(**kwargs))
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in _UNAVAILABLE_CODES:
                logger.error(
                    "dynamodb unavailable", extra={"operation": operation, "error_code": code}
                )
                raise JobStoreUnavailableError(details={"operation": operation}) from exc
            # Condition failures are expected control flow handled by callers;
            # anything else is a programming error. Both propagate unchanged.
            if code not in {
                "ConditionalCheckFailedException",
                "TransactionCanceledException",
                "ResourceNotFoundException",  # expected by ensure_table()
            }:
                logger.error(
                    "dynamodb request rejected", extra={"operation": operation, "error_code": code}
                )
            raise
        except BotoCoreError as exc:
            logger.error(
                "dynamodb call failed",
                extra={"operation": operation, "error": type(exc).__name__},
            )
            raise JobStoreUnavailableError(details={"operation": operation}) from exc

    def _event(self, event_type: str, **detail: Any) -> dict[str, Any]:
        event = {"at": _iso(self._clock()), "type": event_type}
        event.update({k: v for k, v in detail.items() if v is not None})
        return event

    # ------------------------------------------------------------------
    # Table lifecycle
    # ------------------------------------------------------------------

    async def ensure_table(self) -> bool:
        """Create the table if missing. Returns True if it was created."""
        try:
            await self._call("describe_table", TableName=self.table_name)
            return False
        except ClientError as exc:
            if exc.response["Error"]["Code"] != "ResourceNotFoundException":
                raise

        await self._call(
            "create_table",
            TableName=self.table_name,
            BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[
                {"AttributeName": "pk", "AttributeType": "S"},
                {"AttributeName": "meeting_pk", "AttributeType": "S"},
                {"AttributeName": "created_at", "AttributeType": "S"},
                {"AttributeName": "job_status", "AttributeType": "S"},
                {"AttributeName": "available_at", "AttributeType": "S"},
            ],
            KeySchema=[{"AttributeName": "pk", "KeyType": "HASH"}],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": GSI_MEETING,
                    "KeySchema": [
                        {"AttributeName": "meeting_pk", "KeyType": "HASH"},
                        {"AttributeName": "created_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": GSI_STATUS,
                    "KeySchema": [
                        {"AttributeName": "job_status", "KeyType": "HASH"},
                        {"AttributeName": "available_at", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
        )
        waiter = self._client.get_waiter("table_exists")
        await run_in_threadpool(lambda: waiter.wait(TableName=self.table_name))
        await self._call(
            "update_time_to_live",
            TableName=self.table_name,
            TimeToLiveSpecification={"Enabled": True, "AttributeName": "expires_at"},
        )
        logger.info("dynamodb table created", extra={"table": self.table_name})
        return True

    async def table_status(self) -> str:
        response = await self._call("describe_table", TableName=self.table_name)
        return response["Table"]["TableStatus"]

    # ------------------------------------------------------------------
    # Submission
    # ------------------------------------------------------------------

    async def create_job(
        self, *, meeting_id: uuid.UUID, owner_id: uuid.UUID, force: bool
    ) -> tuple[JobRecord, bool]:
        """Queue a job, or return the meeting's already-active job.

        Returns (job, created). Safe to call repeatedly: a double-clicked
        button or a client retry gets the same job back instead of starting
        a second, duplicate LLM run.
        """
        for _ in range(2):
            now = self._clock()
            job_id = str(uuid.uuid4())
            item = {
                "pk": _job_pk(job_id),
                "entity": "job",
                "job_id": job_id,
                "meeting_id": str(meeting_id),
                "owner_id": str(owner_id),
                "meeting_pk": f"MEETING#{meeting_id}",
                "job_status": JobStatus.QUEUED.value,
                "force": force,
                "attempts": 0,
                "max_attempts": self._max_attempts,
                "created_at": _iso(now),
                "updated_at": _iso(now),
                "available_at": _iso(now),
                "events": [self._event("queued", force=force)],
                "expires_at": int((now + timedelta(days=self._ttl_days)).timestamp()),
            }
            lock = {
                "pk": _lock_pk(str(meeting_id)),
                "entity": "lock",
                "job_id": job_id,
                "created_at": _iso(now),
                "expires_at": item["expires_at"],
            }
            try:
                await self._call(
                    "transact_write_items",
                    TransactItems=[
                        {
                            "Put": {
                                "TableName": self.table_name,
                                "Item": _to_ddb(item),
                                "ConditionExpression": "attribute_not_exists(pk)",
                            }
                        },
                        {
                            "Put": {
                                "TableName": self.table_name,
                                "Item": _to_ddb(lock),
                                "ConditionExpression": "attribute_not_exists(pk)",
                            }
                        },
                    ],
                )
                logger.info(
                    "job queued",
                    extra={"job_id": job_id, "meeting_id": str(meeting_id), "force": force},
                )
                return JobRecord.from_item(_to_ddb(item)), True
            except ClientError as exc:
                if exc.response["Error"]["Code"] != "TransactionCanceledException":
                    raise

            # The lock exists: some job is already active for this meeting.
            existing = await self._active_job_for_meeting(str(meeting_id))
            if existing is not None:
                return existing, False
            # Lock pointed at a missing or finished job. Cannot happen through
            # this class (lock release is transactional), but a manually edited
            # table must not block a meeting forever: clear it and try once more.

        raise JobStoreUnavailableError("Could not acquire the meeting's job lock.")

    async def _active_job_for_meeting(self, meeting_id: str) -> JobRecord | None:
        response = await self._call(
            "get_item",
            TableName=self.table_name,
            Key=_to_ddb({"pk": _lock_pk(meeting_id)}),
            ConsistentRead=True,
        )
        lock = response.get("Item")
        if lock is None:
            return None
        lock_job_id = _from_ddb(lock)["job_id"]
        job = await self.get_job(lock_job_id)
        if job is not None and not job.is_terminal:
            return job

        logger.warning(
            "clearing orphaned meeting lock",
            extra={"meeting_id": meeting_id, "job_id": lock_job_id},
        )
        try:
            await self._call(
                "delete_item",
                TableName=self.table_name,
                Key=_to_ddb({"pk": _lock_pk(meeting_id)}),
                ConditionExpression="job_id = :j",
                ExpressionAttributeValues=_to_ddb({":j": lock_job_id}),
            )
        except ClientError:
            pass  # someone else already replaced it
        return None

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    async def get_job(self, job_id: str) -> JobRecord | None:
        response = await self._call(
            "get_item",
            TableName=self.table_name,
            Key=_to_ddb({"pk": _job_pk(job_id)}),
            ConsistentRead=True,
        )
        item = response.get("Item")
        if item is None or _from_ddb(item).get("entity") != "job":
            return None
        return JobRecord.from_item(item)

    async def list_jobs_for_meeting(
        self, meeting_id: uuid.UUID, limit: int = 20
    ) -> list[JobRecord]:
        response = await self._call(
            "query",
            TableName=self.table_name,
            IndexName=GSI_MEETING,
            KeyConditionExpression="meeting_pk = :m",
            ExpressionAttributeValues=_to_ddb({":m": f"MEETING#{meeting_id}"}),
            ScanIndexForward=False,
            Limit=limit,
        )
        return [JobRecord.from_item(i) for i in response.get("Items", [])]

    # ------------------------------------------------------------------
    # Worker operations
    # ------------------------------------------------------------------

    async def claim_next(self, *, worker_id: str, lease_seconds: int) -> JobRecord | None:
        """Atomically take ownership of the oldest runnable job, if any.

        The index query is only a candidate list (and eventually consistent).
        Ownership is decided by the conditional update on the base table, which
        succeeds for exactly one worker even if several race for the same job.
        """
        now = self._clock()
        response = await self._call(
            "query",
            TableName=self.table_name,
            IndexName=GSI_STATUS,
            KeyConditionExpression="job_status = :q AND available_at <= :now",
            ExpressionAttributeValues=_to_ddb({":q": JobStatus.QUEUED.value, ":now": _iso(now)}),
            ScanIndexForward=True,  # oldest first: FIFO
            Limit=_CLAIM_BATCH,
        )
        for candidate in response.get("Items", []):
            c = _from_ddb(candidate)
            lease_until = _iso(now + timedelta(seconds=lease_seconds))
            try:
                updated = await self._call(
                    "update_item",
                    TableName=self.table_name,
                    Key=_to_ddb({"pk": _job_pk(c["job_id"])}),
                    UpdateExpression=(
                        "SET job_status = :p, available_at = :lease, worker_id = :w, "
                        "updated_at = :now, started_at = if_not_exists(started_at, :now), "
                        "events = list_append(events, :ev) ADD attempts :one"
                    ),
                    ConditionExpression="job_status = :q AND available_at = :seen",
                    ExpressionAttributeValues=_to_ddb(
                        {
                            ":p": JobStatus.PROCESSING.value,
                            ":q": JobStatus.QUEUED.value,
                            ":lease": lease_until,
                            ":w": worker_id,
                            ":now": _iso(now),
                            ":seen": c["available_at"],
                            ":one": 1,
                            ":ev": [
                                self._event(
                                    "started", worker_id=worker_id, attempt=int(c["attempts"]) + 1
                                )
                            ],
                        }
                    ),
                    ReturnValues="ALL_NEW",
                )
            except ClientError as exc:
                if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                    continue  # another worker won this one
                raise
            job = JobRecord.from_item(updated["Attributes"])
            logger.info(
                "job claimed",
                extra={"job_id": job.job_id, "worker_id": worker_id, "attempt": job.attempts},
            )
            return job
        return None

    async def renew_lease(self, *, job_id: str, worker_id: str, lease_seconds: int) -> bool:
        """Heartbeat. False means this worker no longer owns the job."""
        now = self._clock()
        try:
            await self._call(
                "update_item",
                TableName=self.table_name,
                Key=_to_ddb({"pk": _job_pk(job_id)}),
                UpdateExpression="SET available_at = :lease, updated_at = :now",
                ConditionExpression="job_status = :p AND worker_id = :w",
                ExpressionAttributeValues=_to_ddb(
                    {
                        ":lease": _iso(now + timedelta(seconds=lease_seconds)),
                        ":now": _iso(now),
                        ":p": JobStatus.PROCESSING.value,
                        ":w": worker_id,
                    }
                ),
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    async def complete(self, *, job: JobRecord, worker_id: str, result: dict[str, Any]) -> bool:
        return await self._finish(
            job=job,
            worker_id=worker_id,
            status=JobStatus.COMPLETED,
            extra={"job_result": result, "error_code": None, "error_message": None},
            event=self._event("completed", cached=result.get("cached")),
        )

    async def fail(
        self, *, job: JobRecord, worker_id: str | None, error_code: str, error_message: str
    ) -> bool:
        return await self._finish(
            job=job,
            worker_id=worker_id,
            status=JobStatus.FAILED,
            extra={"error_code": error_code, "error_message": error_message},
            event=self._event("failed", error_code=error_code),
        )

    async def _finish(
        self,
        *,
        job: JobRecord,
        worker_id: str | None,
        status: JobStatus,
        extra: dict[str, Any],
        event: dict[str, Any],
    ) -> bool:
        """Move a job to a terminal state and release the meeting lock, atomically.

        Conditional on this worker still owning the job. If its lease lapsed and
        the job was recovered elsewhere, the stale worker's write is rejected
        rather than overwriting the newer attempt's outcome.
        """
        now = _iso(self._clock())
        values: dict[str, Any] = {
            ":s": status.value,
            ":now": now,
            ":ev": [event],
            ":p": JobStatus.PROCESSING.value,
            ":j": job.job_id,
        }
        sets = ["job_status = :s", "updated_at = :now", "finished_at = :now", "available_at = :now"]
        removes: list[str] = []
        # Placeholders (#x0) for caller-supplied attribute names: DynamoDB has
        # ~570 reserved words ("result", "status", "name", ...) that fail when
        # used literally in an expression.
        names: dict[str, str] = {}
        for i, (name, value) in enumerate(extra.items()):
            names[f"#x{i}"] = name
            if value is None:
                removes.append(f"#x{i}")
            else:
                values[f":x{i}"] = value
                sets.append(f"#x{i} = :x{i}")
        sets.append("events = list_append(events, :ev)")
        update_expression = "SET " + ", ".join(sets)
        if removes:
            update_expression += " REMOVE " + ", ".join(removes)

        condition = "job_status = :p"
        if worker_id is not None:
            condition += " AND worker_id = :w"
            values[":w"] = worker_id

        try:
            await self._call(
                "transact_write_items",
                TransactItems=[
                    {
                        "Update": {
                            "TableName": self.table_name,
                            "Key": _to_ddb({"pk": _job_pk(job.job_id)}),
                            "UpdateExpression": update_expression,
                            "ConditionExpression": condition,
                            "ExpressionAttributeNames": names,
                            "ExpressionAttributeValues": _to_ddb(
                                {k: v for k, v in values.items() if k != ":j"}
                            ),
                        }
                    },
                    {
                        "Delete": {
                            "TableName": self.table_name,
                            "Key": _to_ddb({"pk": _lock_pk(job.meeting_id)}),
                            "ConditionExpression": "job_id = :j",
                            "ExpressionAttributeValues": _to_ddb({":j": job.job_id}),
                        }
                    },
                ],
            )
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "TransactionCanceledException":
                logger.warning(
                    "job finish rejected, worker no longer owns it",
                    extra={"job_id": job.job_id, "worker_id": worker_id, "status": status.value},
                )
                return False
            raise
        logger.info("job finished", extra={"job_id": job.job_id, "status": status.value})
        return True

    async def requeue(
        self,
        *,
        job: JobRecord,
        worker_id: str,
        delay_seconds: int,
        error_code: str,
        error_message: str,
    ) -> bool:
        """Release the job for a later retry. The meeting lock is kept."""
        now = self._clock()
        retry_at = _iso(now + timedelta(seconds=delay_seconds))
        try:
            await self._call(
                "update_item",
                TableName=self.table_name,
                Key=_to_ddb({"pk": _job_pk(job.job_id)}),
                UpdateExpression=(
                    "SET job_status = :q, available_at = :retry, updated_at = :now, "
                    "error_code = :ec, error_message = :em, events = list_append(events, :ev) "
                    "REMOVE worker_id"
                ),
                ConditionExpression="job_status = :p AND worker_id = :w",
                ExpressionAttributeValues=_to_ddb(
                    {
                        ":q": JobStatus.QUEUED.value,
                        ":p": JobStatus.PROCESSING.value,
                        ":retry": retry_at,
                        ":now": _iso(now),
                        ":ec": error_code,
                        ":em": error_message,
                        ":w": worker_id,
                        ":ev": [
                            self._event("retry_scheduled", error_code=error_code, retry_at=retry_at)
                        ],
                    }
                ),
            )
            logger.info(
                "job requeued",
                extra={"job_id": job.job_id, "error_code": error_code, "retry_at": retry_at},
            )
            return True
        except ClientError as exc:
            if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                return False
            raise

    async def recover_stale_jobs(self) -> list[RecoveredJob]:
        """Find PROCESSING jobs whose lease lapsed (their worker died) and reset them."""
        now = self._clock()
        response = await self._call(
            "query",
            TableName=self.table_name,
            IndexName=GSI_STATUS,
            KeyConditionExpression="job_status = :p AND available_at < :now",
            ExpressionAttributeValues=_to_ddb(
                {":p": JobStatus.PROCESSING.value, ":now": _iso(now)}
            ),
            Limit=_CLAIM_BATCH,
        )
        recovered: list[RecoveredJob] = []
        for item in response.get("Items", []):
            job = JobRecord.from_item(item)
            if job.attempts < job.max_attempts:
                try:
                    await self._call(
                        "update_item",
                        TableName=self.table_name,
                        Key=_to_ddb({"pk": _job_pk(job.job_id)}),
                        UpdateExpression=(
                            "SET job_status = :q, available_at = :now, updated_at = :now, "
                            "error_code = :ec, error_message = :em, "
                            "events = list_append(events, :ev) REMOVE worker_id"
                        ),
                        # Matching the lease we saw guarantees we do not reset a
                        # job whose worker renewed it a moment ago.
                        ConditionExpression="job_status = :p AND available_at = :seen",
                        ExpressionAttributeValues=_to_ddb(
                            {
                                ":q": JobStatus.QUEUED.value,
                                ":p": JobStatus.PROCESSING.value,
                                ":now": _iso(now),
                                ":seen": job.available_at,
                                ":ec": "lease_expired",
                                ":em": "The worker stopped responding; the job was requeued.",
                                ":ev": [
                                    self._event("lease_expired_requeued", worker_id=job.worker_id)
                                ],
                            }
                        ),
                    )
                except ClientError as exc:
                    if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
                        continue
                    raise
                job.status = JobStatus.QUEUED
                recovered.append(RecoveredJob(job=job, requeued=True))
            else:
                if await self.fail(
                    job=job,
                    worker_id=None,
                    error_code="lease_expired",
                    error_message="The worker stopped responding and no attempts remain.",
                ):
                    job.status = JobStatus.FAILED
                    recovered.append(RecoveredJob(job=job, requeued=False))
            logger.warning(
                "stale job recovered",
                extra={"job_id": job.job_id, "attempts": job.attempts, "status": job.status.value},
            )
        return recovered


@lru_cache
def _build_job_store() -> JobStore:
    from app.services.dynamo import get_dynamodb_client

    return JobStore(
        client=get_dynamodb_client(),
        table_name=settings.dynamodb_jobs_table,
        max_attempts=settings.job_max_attempts,
        ttl_days=settings.job_ttl_days,
    )


def get_job_store() -> JobStore:
    """FastAPI dependency; tests override it with a store on a test table."""
    return _build_job_store()
