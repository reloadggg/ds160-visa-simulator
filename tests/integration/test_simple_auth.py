from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient

from app.core import settings as settings_module
from app.core.simple_auth import LOGIN_FAILURES
from app.main import app


ORIGIN = "http://testserver"


@pytest.fixture
def client() -> Generator[TestClient, None, None]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def enabled_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    LOGIN_FAILURES.clear()
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "test-password")
    monkeypatch.setattr(settings_module.settings, "app_auth_session_ttl_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_idle_timeout_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_samesite", "lax")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_domain", None)
    monkeypatch.setattr(settings_module.settings, "app_auth_login_rate_limit_attempts", 2)
    monkeypatch.setattr(settings_module.settings, "app_auth_login_rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", True)
    monkeypatch.setattr(settings_module.settings, "app_auth_protect_docs", True)
    monkeypatch.setattr(settings_module.settings, "app_compat_api_key", None)


def login(client: TestClient) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def test_business_api_requires_auth_when_password_configured(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401
    assert response.json() == {"detail": "authentication required"}


def test_login_sets_cookie_and_allows_business_api(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    assert response.json() == {"authenticated": True, "expires_in": 3600}
    cookie_header = response.headers["set-cookie"]
    assert settings_module.settings.app_auth_cookie_name in cookie_header
    assert "HttpOnly" in cookie_header
    assert "SameSite=lax" in cookie_header

    session_response = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )
    assert session_response.status_code == 201


def test_query_token_no_longer_authenticates_requests(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.post(
        "/v1/sessions?access_token=anything",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401


def test_csrf_origin_is_required_for_cookie_authenticated_writes(
    client: TestClient,
    enabled_auth: None,
) -> None:
    login(client)

    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 403
    assert response.json() == {"detail": "csrf validation failed"}


def test_logout_revokes_cookie_session(
    client: TestClient,
    enabled_auth: None,
) -> None:
    login(client)
    logout_response = client.post("/v1/auth/logout", headers={"Origin": ORIGIN})
    assert logout_response.status_code == 200

    response = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 401


def test_me_reports_cookie_session_status(
    client: TestClient,
    enabled_auth: None,
) -> None:
    assert client.get("/v1/auth/me").json() == {"authenticated": False, "expires_at": None}

    login(client)
    response = client.get("/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["expires_at"].endswith("Z")


def test_invalid_password_is_rejected_and_rate_limited(
    client: TestClient,
    enabled_auth: None,
) -> None:
    for _ in range(2):
        response = client.post(
            "/v1/auth/login",
            json={"password": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid credentials"}

    response = client.post(
        "/v1/auth/login",
        json={"password": "wrong"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "too many login attempts"}


def test_docs_are_protected_when_auth_is_enabled(
    client: TestClient,
    enabled_auth: None,
) -> None:
    response = client.get("/openapi.json")

    assert response.status_code == 401


def test_machine_api_can_use_separate_compat_token(
    client: TestClient,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "app_compat_api_key", "compat-secret")

    response = client.post(
        "/v1/chat/completions",
        json={"model": "test", "messages": []},
        headers={"Authorization": "Bearer compat-secret"},
    )

    assert response.status_code == 422


def test_auth_is_disabled_without_password(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    assert client.get("/v1/auth/me").json() == {"authenticated": True, "expires_at": None}


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
