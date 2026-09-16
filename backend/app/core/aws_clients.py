"""The only place in the application that creates AWS SDK clients (ADR 0010).

Why this exists
---------------
boto3 decides where to send requests and which credentials to use from a long
fallback chain: an explicit endpoint, else real AWS; explicit keys, else
environment variables, else ``~/.aws/credentials``, else an instance role. On a
developer laptop that also holds real AWS credentials, a single empty setting
(``S3_ENDPOINT_URL=``) would silently send local development traffic, including
meeting recordings, to a real AWS account.

``STORAGE_BACKEND`` makes the target an explicit choice instead of a fallback:

``local`` (development, the default)
    * every endpoint must be a local host: loopback, ``host.docker.internal``,
      or a single-label Docker service name. AWS domains are rejected.
    * explicit keys are required; the SDK credential chain is never consulted.
    * clients run in an isolated botocore session whose shared credentials and
      config files point at the null device, so ``~/.aws`` is never read.
    * every outgoing request is checked against the configured endpoint host,
      and refused otherwise. This guard is the last line of defence even if a
      client is somehow built with the wrong endpoint.
    Misconfiguration fails at start-up, before any request is made.

``aws`` (deployment)
    Real S3 and DynamoDB through the normal credential chain / IAM role. It is
    guarded: selecting it is a deliberate deployment change, and any attempt to
    use it outside that configuration fails at start-up with a clear message.
"""

from __future__ import annotations

import enum
import ipaddress
import os
from typing import Any
from urllib.parse import urlsplit

import boto3
import botocore.session
from botocore.config import Config


class StorageBackend(enum.StrEnum):
    LOCAL = "local"
    AWS = "aws"


class UnsafeAwsConfigurationError(RuntimeError):
    """Raised when a configuration or request could reach real AWS in local mode."""


# Hostnames that can only ever mean "this machine or its Docker network".
_LOCAL_HOSTNAMES = frozenset({"localhost", "host.docker.internal"})

# Suffixes of real AWS service endpoints, including China and the newer
# dual-stack/api.aws domains. Rejected explicitly even though the general rule
# below would also reject them, so the error message is unambiguous.
_AWS_DOMAIN_SUFFIXES = (".amazonaws.com", ".amazonaws.com.cn", ".api.aws", ".on.aws")


def endpoint_host(url: str) -> str:
    return (urlsplit(url).hostname or "").lower()


def local_endpoint_problem(url: str | None, setting_name: str) -> str | None:
    """Return why ``url`` is not an acceptable local endpoint, or None if it is."""
    if not url or not url.strip():
        return f"{setting_name} is empty. In local mode an empty endpoint would mean real AWS."
    parts = urlsplit(url.strip())
    if parts.scheme not in ("http", "https"):
        return f"{setting_name} must start with http:// or https:// (got {url!r})."
    host = (parts.hostname or "").lower()
    if not host:
        return f"{setting_name} has no host (got {url!r})."
    if host.endswith(_AWS_DOMAIN_SUFFIXES) or host in {"amazonaws.com", "api.aws"}:
        return f"{setting_name} points at real AWS ({host}), which local mode forbids."
    if host in _LOCAL_HOSTNAMES:
        return None
    try:
        if ipaddress.ip_address(host).is_loopback:
            return None
        return f"{setting_name} uses a non-loopback IP address ({host}); local mode allows loopback only."
    except ValueError:
        pass
    if "." not in host:
        return None  # single-label name: a Docker Compose service such as "object-storage"
    return (
        f"{setting_name} uses the external hostname {host!r}. Local mode allows only "
        "localhost, loopback addresses, host.docker.internal, or a Docker service name."
    )


def _isolated_session(access_key: str, secret_key: str, region: str) -> boto3.session.Session:
    """A boto3 session that cannot discover credentials or config from the machine."""
    core = botocore.session.Session()
    # Read shared files from the null device instead of ~/.aws/credentials and
    # ~/.aws/config. Explicit keys already bypass the credential chain; this
    # additionally removes profile-level settings (endpoints, regions, roles)
    # that could otherwise redirect a client.
    core.set_config_variable("credentials_file", os.devnull)
    core.set_config_variable("config_file", os.devnull)
    # Stop AWS_PROFILE from selecting a profile. Without this, a developer shell
    # with AWS_PROFILE set crashes local mode (ProfileNotFound), since the
    # profile cannot exist in the null config file. Found by
    # test_local_client_uses_explicit_keys_never_the_shared_credentials_file.
    core.session_var_map["profile"] = (None, None, None, None)
    return boto3.session.Session(
        aws_access_key_id=access_key,
        aws_secret_access_key=secret_key,
        region_name=region,
        botocore_session=core,
    )


def _pin_requests_to(client: Any, allowed_host: str) -> None:
    """Refuse any request whose destination is not the configured local host."""

    def guard(request: Any, **_: Any) -> None:
        host = endpoint_host(request.url)
        if host != allowed_host:
            raise UnsafeAwsConfigurationError(
                f"Blocked a request to {host!r}: local storage mode only permits {allowed_host!r}."
            )

    client.meta.events.register_first("before-send.*.*", guard)


def build_client(
    service: str,
    *,
    backend: StorageBackend,
    endpoint_url: str | None,
    region: str,
    access_key: str | None,
    secret_key: str | None,
    config: Config,
    setting_name: str,
) -> Any:
    """Create an S3 or DynamoDB client that honours ``STORAGE_BACKEND``."""
    if backend is StorageBackend.AWS:
        raise UnsafeAwsConfigurationError(
            "STORAGE_BACKEND=aws is guarded and cannot be selected in this configuration."
        )

    problem = local_endpoint_problem(endpoint_url, setting_name)
    if problem:
        raise UnsafeAwsConfigurationError(problem)
    if not access_key or not secret_key:
        raise UnsafeAwsConfigurationError(
            f"Local mode requires explicit credentials for {service}; without them the AWS SDK "
            "would fall back to ~/.aws/credentials."
        )

    assert endpoint_url is not None
    session = _isolated_session(access_key, secret_key, region)
    client = session.client(service, endpoint_url=endpoint_url.strip(), config=config)
    _pin_requests_to(client, endpoint_host(endpoint_url))
    return client
