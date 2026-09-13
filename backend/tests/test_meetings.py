"""Meeting CRUD and authorization tests.

The authorization tests are the most important in the M1 suite: they encode the
guarantee that a user can never reach another user's meeting, even while
holding a valid token and the exact meeting id.
"""

import uuid

from httpx import AsyncClient


async def test_create_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)

    response = await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    assert response.status_code == 201

    body = response.json()
    assert body["title"] == "Sprint planning"
    assert body["status"] == "created"
    assert body["source_type"] == "text"
    # Ownership comes from the token, not the request body.
    assert body["owner_id"] == str(user.id)


async def test_create_meeting_ignores_owner_id_in_body(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    """A client must not be able to create a meeting owned by someone else."""
    attacker = await make_user()
    victim = await make_user()
    headers = await auth_headers(attacker)

    response = await client.post(
        "/api/v1/meetings",
        json=meeting_payload(owner_id=str(victim.id)),
        headers=headers,
    )
    assert response.status_code == 201
    assert response.json()["owner_id"] == str(attacker.id)


async def test_create_meeting_requires_authentication(client: AsyncClient, meeting_payload) -> None:
    response = await client.post("/api/v1/meetings", json=meeting_payload())
    assert response.status_code == 401


async def test_create_meeting_rejects_blank_title(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)

    response = await client.post(
        "/api/v1/meetings", json=meeting_payload(title="   "), headers=headers
    )
    assert response.status_code == 422


async def test_list_returns_only_my_meetings(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    alice = await make_user()
    bob = await make_user()
    alice_headers = await auth_headers(alice)
    bob_headers = await auth_headers(bob)

    await client.post(
        "/api/v1/meetings", json=meeting_payload(title="Alice 1"), headers=alice_headers
    )
    await client.post(
        "/api/v1/meetings", json=meeting_payload(title="Alice 2"), headers=alice_headers
    )
    await client.post("/api/v1/meetings", json=meeting_payload(title="Bob 1"), headers=bob_headers)

    response = await client.get("/api/v1/meetings", headers=alice_headers)
    assert response.status_code == 200
    body = response.json()

    assert body["total"] == 2
    assert {m["title"] for m in body["items"]} == {"Alice 1", "Alice 2"}


async def test_list_pagination(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    for i in range(5):
        await client.post("/api/v1/meetings", json=meeting_payload(title=f"M{i}"), headers=headers)

    page1 = (await client.get("/api/v1/meetings?page=1&size=2", headers=headers)).json()
    page3 = (await client.get("/api/v1/meetings?page=3&size=2", headers=headers)).json()

    assert page1["total"] == 5
    assert len(page1["items"]) == 2
    assert len(page3["items"]) == 1


async def test_get_own_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    ).json()

    response = await client.get(f"/api/v1/meetings/{created['id']}", headers=headers)
    assert response.status_code == 200
    assert response.json()["id"] == created["id"]


async def test_user_cannot_read_another_users_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    """The headline authorization guarantee.

    Bob holds a valid token AND the exact meeting id. He must still get 404 -
    not 403, which would confirm the meeting exists.
    """
    alice = await make_user()
    bob = await make_user()

    alice_headers = await auth_headers(alice)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=alice_headers)
    ).json()

    bob_headers = await auth_headers(bob)
    response = await client.get(f"/api/v1/meetings/{created['id']}", headers=bob_headers)

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


async def test_user_cannot_update_another_users_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    alice = await make_user()
    bob = await make_user()
    alice_headers = await auth_headers(alice)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=alice_headers)
    ).json()

    bob_headers = await auth_headers(bob)
    response = await client.patch(
        f"/api/v1/meetings/{created['id']}", json={"title": "Hijacked"}, headers=bob_headers
    )
    assert response.status_code == 404

    # And the record is untouched.
    still = (await client.get(f"/api/v1/meetings/{created['id']}", headers=alice_headers)).json()
    assert still["title"] == "Sprint planning"


async def test_user_cannot_delete_another_users_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    alice = await make_user()
    bob = await make_user()
    alice_headers = await auth_headers(alice)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=alice_headers)
    ).json()

    bob_headers = await auth_headers(bob)
    assert (
        await client.delete(f"/api/v1/meetings/{created['id']}", headers=bob_headers)
    ).status_code == 404
    assert (
        await client.get(f"/api/v1/meetings/{created['id']}", headers=alice_headers)
    ).status_code == 200


async def test_get_missing_meeting_returns_404(
    client: AsyncClient, make_user, auth_headers
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    response = await client.get(f"/api/v1/meetings/{uuid.uuid4()}", headers=headers)
    assert response.status_code == 404


async def test_malformed_uuid_returns_422(client: AsyncClient, make_user, auth_headers) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    response = await client.get("/api/v1/meetings/not-a-uuid", headers=headers)
    assert response.status_code == 422


async def test_partial_update(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    ).json()

    response = await client.patch(
        f"/api/v1/meetings/{created['id']}", json={"title": "Renamed"}, headers=headers
    )
    assert response.status_code == 200
    body = response.json()

    assert body["title"] == "Renamed"
    # Unsupplied fields are preserved.
    assert body["description"] == "Planning the next sprint"


async def test_empty_patch_changes_nothing(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    ).json()

    response = await client.patch(f"/api/v1/meetings/{created['id']}", json={}, headers=headers)
    assert response.status_code == 200
    assert response.json()["title"] == created["title"]


async def test_delete_meeting(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    user = await make_user()
    headers = await auth_headers(user)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    ).json()

    assert (
        await client.delete(f"/api/v1/meetings/{created['id']}", headers=headers)
    ).status_code == 204
    assert (
        await client.get(f"/api/v1/meetings/{created['id']}", headers=headers)
    ).status_code == 404


async def test_patch_rejects_explicit_null_for_required_fields(
    client: AsyncClient, make_user, auth_headers, meeting_payload
) -> None:
    """Regression: {"title": null} used to reach the database and return 500."""
    user = await make_user()
    headers = await auth_headers(user)
    created = (
        await client.post("/api/v1/meetings", json=meeting_payload(), headers=headers)
    ).json()

    response = await client.patch(
        f"/api/v1/meetings/{created['id']}", json={"title": None}, headers=headers
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"
