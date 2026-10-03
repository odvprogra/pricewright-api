"""Sign in through the fully wired application: PostgreSQL, argon2id and real JWTs."""

import httpx
import pytest

from tests.integration.data import ADMIN_EMAIL, ADMIN_PASSWORD

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("session_factory")]


async def test_sign_in_refresh_and_detect_a_replayed_refresh_token(
    northfield_client: httpx.AsyncClient,
) -> None:
    client = northfield_client
    login = await client.post(
        "/api/v1/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}
    )
    token = login.json()["access_token"]
    me = await client.get("/api/v1/me", headers={"Authorization": f"Bearer {token}"})
    first_refresh = login.json()["refresh_token"]
    refreshed = await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    replayed = await client.post("/api/v1/auth/refresh", json={"refresh_token": first_refresh})
    after_reuse = await client.post(
        "/api/v1/auth/refresh", json={"refresh_token": refreshed.json()["refresh_token"]}
    )

    assert login.status_code == 200
    assert me.status_code == 200
    assert me.json()["email"] == ADMIN_EMAIL
    assert me.json()["role"] == "admin"
    assert refreshed.status_code == 200
    assert replayed.status_code == 401  # reuse detected: the whole session ends
    assert after_reuse.status_code == 401
