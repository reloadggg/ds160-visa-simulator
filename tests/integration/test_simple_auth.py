from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core import settings as settings_module
from app.main import app


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def enabled_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "test-password")
    monkeypatch.setattr(settings_module.settings, "app_auth_token_ttl_seconds", 3600)


def test_business_api_requires_auth_when_password_configured(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}


def test_login_returns_bearer_token_for_valid_password(
    client: TestClient,
    enabled_auth: None,
) -> None:
    login_response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
    )

    assert login_response.status_code == 200
    payload = login_response.json()
    assert payload["token_type"] == "bearer"
    assert payload["expires_in"] == 3600
    assert payload["access_token"]

    response = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Authorization": f"Bearer {payload['access_token']}"},
    )
    assert response.status_code == 201


def test_query_token_allows_browser_asset_requests(
    client: TestClient,
    enabled_auth: None,
) -> None:
    login_response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
    )
    token = login_response.json()["access_token"]

    response = client.post(
        f"/v1/sessions?access_token={token}",
        json={"declared_family": "f1"},
    )

    assert response.status_code == 201


def test_invalid_password_is_rejected(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.post("/v1/auth/login", json={"password": "wrong"})

    assert response.status_code == 401
    assert response.json() == {"detail": "invalid password"}


def test_auth_is_disabled_without_password(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201


def test_debug_fill_is_disabled_by_default(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_debug_fill", False)
    session_response = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_response.json()["session_id"]

    response = client.post(f"/v1/sessions/{session_id}/debug/fill-current-gap")

    assert response.status_code == 403
    assert response.json() == {"detail": "debug fill is disabled"}
