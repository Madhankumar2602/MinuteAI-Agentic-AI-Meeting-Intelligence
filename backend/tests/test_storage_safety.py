"""Local development must never be able to reach real AWS (ADR 0010).

These tests make no network calls to AWS. Where a request is attempted, it is
aimed at a real AWS hostname specifically to prove the guard stops it before a
single byte leaves the machine.
"""

import ast
import os
from pathlib import Path

import pytest
from botocore.config import Config
from pydantic import ValidationError

from app.core.aws_clients import (
    StorageBackend,
    UnsafeAwsConfigurationError,
    build_client,
    local_endpoint_problem,
)
from app.core.config import Settings, settings

APP_DIR = Path(__file__).resolve().parents[1] / "app"

LOCAL = {
    "storage_backend": "local",
    "s3_endpoint_url": "http://localhost:9000",
    "dynamodb_endpoint_url": "http://localhost:8001",
    "s3_access_key_id": "local-s3",
    "s3_secret_access_key": "local-s3-secret",
    "aws_access_key_id": "local",
    "aws_secret_access_key": "local",
    "postgres_user": "u",
    "postgres_password": "p",
    "postgres_db": "d",
    "jwt_secret": "x" * 40,
}

AWS_ENDPOINTS = [
    "https://s3.amazonaws.com",
    "https://s3.us-east-1.amazonaws.com",
    "https://minuteai-media.s3.eu-west-1.amazonaws.com",
    "https://dynamodb.ap-south-1.amazonaws.com",
    "https://s3.cn-north-1.amazonaws.com.cn",
    "https://s3.dualstack.us-east-1.api.aws",
]


def make_settings(**overrides) -> Settings:
    # _env_file=None: judge only the values given, not the developer's .env.
    return Settings(_env_file=None, **{**LOCAL, **overrides})  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# The running configuration
# ---------------------------------------------------------------------------


def test_this_environment_runs_in_local_mode() -> None:
    assert settings.storage_backend is StorageBackend.LOCAL
    assert local_endpoint_problem(settings.s3_endpoint_url, "S3") is None
    assert local_endpoint_problem(settings.dynamodb_endpoint_url, "DYNAMODB") is None


def test_local_is_the_default_when_the_variable_is_missing() -> None:
    values = {k: v for k, v in LOCAL.items() if k != "storage_backend"}
    assert Settings(_env_file=None, **values).storage_backend is StorageBackend.LOCAL  # type: ignore[call-arg]


# ---------------------------------------------------------------------------
# Start-up validation: fail fast
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("endpoint", AWS_ENDPOINTS)
@pytest.mark.parametrize(
    "setting", ["s3_endpoint_url", "dynamodb_endpoint_url", "s3_public_endpoint_url"]
)
def test_local_mode_rejects_real_aws_endpoints(setting: str, endpoint: str) -> None:
    with pytest.raises(ValidationError, match="real AWS"):
        make_settings(**{setting: endpoint})


@pytest.mark.parametrize("setting", ["s3_endpoint_url", "dynamodb_endpoint_url"])
def test_local_mode_rejects_an_empty_endpoint(setting: str) -> None:
    """An empty endpoint is exactly what makes boto3 default to real AWS."""
    with pytest.raises(ValidationError, match="empty"):
        make_settings(**{setting: ""})


@pytest.mark.parametrize(
    "endpoint",
    [
        "https://storage.example.com",
        "http://10.0.0.5:9000",
        "http://192.168.1.20:9000",
        "ftp://localhost:9000",
    ],
)
def test_local_mode_rejects_non_local_hosts(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        make_settings(s3_endpoint_url=endpoint)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://localhost:9000",
        "http://127.0.0.1:9000",
        "http://[::1]:9000",
        "http://host.docker.internal:9000",
        "http://object-storage:9000",  # Docker Compose service name
    ],
)
def test_local_mode_accepts_local_hosts(endpoint: str) -> None:
    assert make_settings(s3_endpoint_url=endpoint).s3_endpoint_url == endpoint


@pytest.mark.parametrize(
    "missing",
    ["s3_access_key_id", "s3_secret_access_key", "aws_access_key_id", "aws_secret_access_key"],
)
def test_local_mode_requires_explicit_credentials(missing: str) -> None:
    """Without explicit keys the SDK would search ~/.aws/credentials."""
    with pytest.raises(ValidationError, match="must be set"):
        make_settings(**{missing: ""})


def test_all_problems_are_reported_together() -> None:
    with pytest.raises(ValidationError) as exc_info:
        make_settings(
            s3_endpoint_url="https://s3.amazonaws.com",
            dynamodb_endpoint_url="",
            s3_access_key_id="",
        )
    message = str(exc_info.value)
    assert (
        "S3_ENDPOINT_URL" in message
        and "DYNAMODB_ENDPOINT_URL" in message
        and "S3_ACCESS_KEY_ID" in message
    )


def test_aws_mode_is_not_enabled_yet() -> None:
    with pytest.raises(ValidationError, match="guarded and cannot be selected"):
        make_settings(storage_backend="aws")


# ---------------------------------------------------------------------------
# Client construction: no fallback to the machine's AWS configuration
# ---------------------------------------------------------------------------


def _build(**overrides):
    args = {
        "backend": StorageBackend.LOCAL,
        "endpoint_url": "http://localhost:9000",
        "region": "us-east-1",
        "access_key": "local-key",
        "secret_key": "local-secret",
        "config": Config(retries={"max_attempts": 1}),
        "setting_name": "S3_ENDPOINT_URL",
    }
    return build_client("s3", **{**args, **overrides})


@pytest.fixture
def real_looking_aws_credentials(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> str:
    """Simulate a laptop that also holds real AWS credentials, everywhere boto3 looks."""
    credentials = tmp_path / "credentials"
    credentials.write_text(
        "[default]\naws_access_key_id = AKIAFROMSHAREDFILE00\naws_secret_access_key = file-secret\n"
    )
    config = tmp_path / "config"
    config.write_text("[default]\nregion = eu-west-1\nendpoint_url = https://s3.amazonaws.com\n")
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(credentials))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(config))
    monkeypatch.setenv("AWS_PROFILE", "a-real-profile-name")
    return "AKIAFROMSHAREDFILE00"


def test_local_client_uses_explicit_keys_never_the_shared_credentials_file(
    real_looking_aws_credentials: str,
) -> None:
    client = _build()
    credentials = client._request_signer._credentials
    assert credentials.access_key == "local-key"
    assert credentials.access_key != real_looking_aws_credentials


def test_local_client_ignores_endpoint_and_region_from_the_shared_config_file(
    real_looking_aws_credentials: str,
) -> None:
    client = _build()
    assert client.meta.endpoint_url == "http://localhost:9000"
    assert client.meta.region_name == "us-east-1"


def test_local_client_session_cannot_discover_any_credentials(
    real_looking_aws_credentials: str,
) -> None:
    """Even asked directly, the isolated session finds nothing on the machine."""
    from app.core.aws_clients import _isolated_session

    session = _isolated_session("local-key", "local-secret", "us-east-1")
    core = session._session
    assert core.get_config_variable("credentials_file") == os.devnull
    assert core.get_config_variable("config_file") == os.devnull


def test_build_client_refuses_missing_keys_before_any_lookup() -> None:
    with pytest.raises(UnsafeAwsConfigurationError, match="explicit credentials"):
        _build(access_key="")


@pytest.mark.parametrize("endpoint", [*AWS_ENDPOINTS, "", None])
def test_build_client_refuses_aws_or_empty_endpoints(endpoint: str | None) -> None:
    with pytest.raises(UnsafeAwsConfigurationError):
        _build(endpoint_url=endpoint)


def test_build_client_refuses_aws_mode_for_now() -> None:
    with pytest.raises(UnsafeAwsConfigurationError, match="guarded and cannot be selected"):
        _build(backend=StorageBackend.AWS)


# ---------------------------------------------------------------------------
# Runtime guard: a request cannot leave for another host
# ---------------------------------------------------------------------------


def test_guard_runs_on_the_real_send_path_and_blocks_a_mismatched_host() -> None:
    """The guard sits on the SDK's final pre-send hook for real API calls.

    The client targets localhost, but the guard is pinned to a different host.
    A real call must be refused by the guard before any connection is opened.
    No AWS hostname is involved, so this cannot contact AWS even if it failed.
    """
    from app.core.aws_clients import _pin_requests_to

    client = _build(config=Config(retries={"max_attempts": 1}, connect_timeout=1, read_timeout=1))
    _pin_requests_to(client, "some-other-host")

    with pytest.raises(UnsafeAwsConfigurationError, match="Blocked a request to 'localhost'"):
        client.list_buckets()


def test_request_to_the_configured_local_host_is_allowed(storage) -> None:
    """The guard does not interfere with legitimate local traffic."""
    assert (
        storage._client.head_bucket(Bucket=storage.bucket)["ResponseMetadata"]["HTTPStatusCode"]
        == 200
    )


def test_application_clients_are_guarded() -> None:
    from app.services.dynamo import get_dynamodb_client
    from app.services.storage import get_storage

    for client in (get_storage()._client, get_dynamodb_client()):
        assert client.meta.endpoint_url.startswith("http://localhost")
        # Emitting a fake request to AWS through the client's own event system
        # must be refused by the registered guard.
        fake_request = type("Req", (), {"url": "https://s3.amazonaws.com/"})()
        with pytest.raises(UnsafeAwsConfigurationError):
            client.meta.events.emit(
                f"before-send.{client.meta.service_model.service_name}.ListBuckets",
                request=fake_request,
            )


# ---------------------------------------------------------------------------
# No other path to AWS in the codebase
# ---------------------------------------------------------------------------


def test_aws_sdk_clients_are_created_only_in_aws_clients_module() -> None:
    """A boto3 client or session created elsewhere would bypass every guard above.

    Importing plain data helpers (e.g. boto3.dynamodb.types) is allowed; creating
    clients, resources, or sessions is not.
    """
    forbidden_calls = {"client", "resource", "Session"}
    forbidden_modules = {"boto3", "boto3.session", "botocore.session"}
    offenders = []
    for path in APP_DIR.rglob("*.py"):
        if path.name == "aws_clients.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        where = path.relative_to(APP_DIR.parent)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                offenders += [
                    f"{where}: import {a.name}" for a in node.names if a.name in forbidden_modules
                ]
            elif isinstance(node, ast.ImportFrom) and node.module in forbidden_modules:
                offenders.append(f"{where}: from {node.module} import ...")
            elif (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in forbidden_calls
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in {"boto3", "botocore"}
            ):
                offenders.append(
                    f"{where}:{node.lineno} calls {node.func.value.id}.{node.func.attr}()"
                )
    assert offenders == []
