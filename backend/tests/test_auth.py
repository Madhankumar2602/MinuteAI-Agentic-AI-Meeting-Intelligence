"""Authentication and account tests."""

from httpx import AsyncClient

from tests.conftest import DEFAULT_PASSWORD


async def test_register_creates_user_and_hides_password(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "email": "New.User@Example.com",
            "password": "a-good-password",
            "full_name": "New User",
        },
    )
    assert response.status_code == 201
    body = response.json()

    assert body["email"] == "new.user@example.com"  # normalised to lower case
    assert body["full_name"] == "New User"
    assert body["is_active"] is True
    # The response model must never expose the hash.
    assert "password" not in body
    assert "password_hash" not in body


async def test_register_rejects_duplicate_email(client: AsyncClient) -> None:
    payload = {"email": "dup@example.com", "password": "a-good-password", "full_name": "Dup"}
    assert (await client.post("/api/v1/auth/register", json=payload)).status_code == 201

    response = await client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "conflict"


async def test_register_rejects_weak_and_malformed_input(client: AsyncClient) -> None:
    response = await client.post(
        "/api/v1/auth/register",
        json={"email": "not-an-email", "password": "short", "full_name": ""},
    )
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "validation_error"
    assert {d["field"] for d in error["details"]} == {
        "body.email",
        "body.password",
        "body.full_name",
    }


async def test_login_returns_a_usable_token(client: AsyncClient, make_user) -> None:
    user = await make_user()

    response = await client.post(
        "/api/v1/auth/login",
        data={"username": user.email, "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["token_type"] == "bearer"
    assert body["access_token"]

    me = await client.get(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {body['access_token']}"},
    )
    assert me.status_code == 200
    assert me.json()["email"] == user.email


async def test_login_is_case_insensitive_on_email(client: AsyncClient, make_user) -> None:
    await make_user(email="mixed.case@example.com")
    response = await client.post(
        "/api/v1/auth/login",
        data={"username": "Mixed.Case@EXAMPLE.com", "password": DEFAULT_PASSWORD},
    )
    assert response.status_code == 200


async def test_wrong_password_and_unknown_email_are_indistinguishable(
    client: AsyncClient, make_user
) -> None:
    """User enumeration guard: both failures must look identical."""
    user = await make_user()

    wrong_password = await client.post(
        "/api/v1/auth/login", data={"username": user.email, "password": "not-the-password"}
    )
    unknown_email = await client.post(
        "/api/v1/auth/login", data={"username": "nobody@example.com", "password": "whatever"}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json()["error"]["message"] == unknown_email.json()["error"]["message"]


async def test_me_requires_authentication(client: AsyncClient) -> None:
    response = await client.get("/api/v1/auth/me")
    assert response.status_code == 401


async def test_me_rejects_a_tampered_token(client: AsyncClient, make_user, auth_headers) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    headers["Authorization"] = headers["Authorization"] + "tampered"

    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401


async def test_me_rejects_a_token_for_a_deleted_user(
    client: AsyncClient, make_user, auth_headers, db_session
) -> None:
    """The token is still cryptographically valid; the user is gone."""
    user = await make_user()
    headers = await auth_headers(user)

    await db_session.delete(user)
    await db_session.commit()

    response = await client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 401
