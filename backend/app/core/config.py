"""Application configuration.

All runtime configuration is loaded from the repository-root ``.env`` file into
a single typed ``Settings`` object. Typing matters here: a missing or malformed
variable raises at import time (application start-up) rather than surfacing as a
confusing runtime error inside a request handler.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict

# config.py -> core -> app -> backend -> <repo root>
REPO_ROOT = Path(__file__).resolve().parents[3]
ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    """Typed view over the environment."""

    model_config = SettingsConfigDict(
        env_file=ENV_FILE,
        env_file_encoding="utf-8",
        case_sensitive=False,
        # Ignore unrelated variables that may exist in the shell environment.
        extra="ignore",
    )

    # ---- Application ------------------------------------------------------
    app_name: str = "MinuteAI"
    app_env: Literal["development", "production"] = "development"
    app_host: str = "0.0.0.0"
    app_port: int = 8010
    api_v1_prefix: str = "/api/v1"

    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"

    # Comma-separated. Kept as a plain string because pydantic-settings tries to
    # JSON-decode list-typed fields, which breaks on `a,b` style values.
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # ---- PostgreSQL -------------------------------------------------------
    postgres_user: str
    postgres_password: str
    postgres_db: str
    postgres_db_test: str = "minuteai_test"
    postgres_host: str = "localhost"
    postgres_port: int = 5432

    # ---- DynamoDB ---------------------------------------------------------
    # Empty string means "use the real AWS endpoint" (M10). Locally this points
    # at the DynamoDB Local container.
    dynamodb_endpoint_url: str = "http://localhost:8001"
    aws_region: str = "us-east-1"
    aws_access_key_id: str = "local"
    aws_secret_access_key: str = "local"

    # Name of the processing-jobs table. Tests use a separate table.
    dynamodb_jobs_table: str = "minuteai_processing_jobs"
    # Create the table on start-up if missing. Convenient locally; in AWS (M10)
    # tables are created by infrastructure code and the app role is not granted
    # dynamodb:CreateTable, so this is turned off there.
    dynamodb_auto_create_tables: bool = True

    # ---- Object storage: S3 (M4, ADR 0009) --------------------------------
    s3_bucket: str = "minuteai-dev-media"
    # Empty = real AWS S3. Locally, the RustFS container.
    s3_endpoint_url: str = "http://localhost:9000"
    # Host placed in presigned URLs for browsers. Empty = same as s3_endpoint_url.
    s3_public_endpoint_url: str = ""
    s3_region: str = "us-east-1"
    # Empty = boto3's default credential chain (the IAM role in AWS).
    s3_access_key_id: str = ""
    s3_secret_access_key: SecretStr = SecretStr("")
    s3_auto_create_bucket: bool = True
    media_max_bytes: int = Field(default=200 * 1024 * 1024, ge=1024)
    media_upload_url_ttl_seconds: int = Field(default=900, ge=60, le=3600)
    gemini_transcription_model: str = "gemini-3.6-flash"

    # ---- Background processing (M3, ADR 0008) -----------------------------
    # Run the worker inside the API process. Set false to run it separately
    # with `python -m app.workers.processing`.
    worker_embedded: bool = True
    worker_concurrency: int = Field(default=2, ge=1, le=16)
    worker_poll_seconds: float = Field(default=2.0, gt=0)
    # A claimed job is owned for this long; the worker renews it while running.
    # If the worker dies, the job becomes claimable again once the lease lapses.
    job_lease_seconds: int = Field(default=120, ge=10)
    job_max_attempts: int = Field(default=3, ge=1, le=10)
    # Job-level retry delay (base * 4^(attempt-1)) for transient provider errors
    # that outlasted the LLM client's own short retries.
    job_retry_base_seconds: int = Field(default=30, ge=1)
    job_ttl_days: int = Field(default=30, ge=1)

    # ---- Authentication ---------------------------------------------------
    jwt_secret: str = Field(min_length=32)
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60

    # ---- LLM provider (ADR 0006) ------------------------------------------
    # SecretStr keeps the key out of repr() and tracebacks; read it only with
    # .get_secret_value() at the single point where the client is built.
    gemini_api_key: SecretStr = SecretStr("")
    gemini_model: str = "gemini-3.6-flash"
    llm_timeout_seconds: float = Field(default=120, gt=0)
    llm_max_retries: int = Field(default=3, ge=0, le=6)

    # Upper bound on accepted transcript size. Protects the database, the LLM
    # quota, and the request body parser from a single oversized upload.
    transcript_max_chars: int = Field(default=400_000, ge=1_000)

    # ---- Derived values ---------------------------------------------------
    @computed_field  # type: ignore[prop-decorator]
    @property
    def database_url(self) -> str:
        """Async SQLAlchemy URL for the development database."""
        return self._build_url(self.postgres_db)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def test_database_url(self) -> str:
        """Async SQLAlchemy URL for the isolated test database."""
        return self._build_url(self.postgres_db_test)

    def _build_url(self, database: str) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{database}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"


@lru_cache
def get_settings() -> Settings:
    """Cached accessor so the .env file is parsed exactly once per process."""
    return Settings()  # type: ignore[call-arg]


settings = get_settings()
