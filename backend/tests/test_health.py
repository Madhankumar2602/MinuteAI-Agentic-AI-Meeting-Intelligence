"""Health endpoint tests."""

from httpx import AsyncClient


async def test_health_is_public_and_ok(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "MinuteAI"


async def test_health_returns_request_id_header(client: AsyncClient) -> None:
    response = await client.get("/health")
    assert response.headers.get("X-Request-ID")


async def test_inbound_request_id_is_echoed_back(client: AsyncClient) -> None:
    """A caller-supplied correlation id must be preserved, not replaced."""
    response = await client.get("/health", headers={"X-Request-ID": "trace-me-123"})
    assert response.headers["X-Request-ID"] == "trace-me-123"


async def test_health_deps_reports_postgres_and_dynamodb(client: AsyncClient) -> None:
    response = await client.get("/health/deps")
    body = response.json()

    # "fake" is the test LLM provider's name; in production this key is "gemini".
    assert set(body["checks"]) == {"postgres", "dynamodb", "s3", "fake"}
    assert body["checks"]["s3"]["healthy"] is True
    assert body["checks"]["postgres"]["healthy"] is True
    # pgvector must be present - M6 depends on it.
    assert "pgvector=yes" in body["checks"]["postgres"]["detail"]
    assert body["checks"]["dynamodb"]["healthy"] is True
    assert response.status_code == 200
    assert body["status"] == "ok"


async def test_unknown_route_uses_the_standard_error_envelope(client: AsyncClient) -> None:
    response = await client.get("/does-not-exist")
    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == "http_error"
    assert error["request_id"]


async def test_health_deps_is_degraded_when_the_llm_is_unhealthy(
    client: AsyncClient, fake_llm
) -> None:
    fake_llm.healthy = False
    response = await client.get("/health/deps")
    assert response.status_code == 503
    body = response.json()
    assert body["status"] == "degraded"
    assert body["checks"]["fake"]["healthy"] is False
