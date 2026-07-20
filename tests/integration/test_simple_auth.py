from collections.abc import Generator
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.core.simple_auth import (
    LOGIN_FAILURES,
    _get_auth_session,
    _hash_secret,
    create_auth_session,
)
from app.db.base import Base
from app.db.models import AuthSessionRecord
from app.db.session import get_db
from app.main import app


ORIGIN = "http://testserver"


@pytest.fixture
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'simple-auth.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture
def client(db_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    app.state.auth_session_factory = db_session_factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.auth_session_factory = None


@pytest.fixture
def enabled_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    LOGIN_FAILURES.clear()
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "test-password")
    monkeypatch.setattr(
        settings_module.settings,
        "app_auth_password_user_fallback_enabled",
        True,
    )
    monkeypatch.setattr(settings_module.settings, "app_auth_session_ttl_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_idle_timeout_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_samesite", "lax")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_domain", None)
    monkeypatch.setattr(settings_module.settings, "app_auth_login_rate_limit_attempts", 2)
    monkeypatch.setattr(settings_module.settings, "app_auth_login_rate_limit_window_seconds", 60)
    monkeypatch.setattr(settings_module.settings, "app_auth_touch_interval_seconds", 60)
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
    assert response.json() == {
        "authenticated": True,
        "expires_in": 3600,
        "history_namespace": "local-dev",
        "access_key_quota": None,
    }
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


def test_expired_cookie_session_cannot_access_business_api(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    login(client)
    session_id = client.cookies.get(settings_module.settings.app_auth_cookie_name)
    assert session_id
    with db_session_factory() as db:
        record = db.get(AuthSessionRecord, _hash_secret(session_id))
        assert record is not None
        record.expires_at = datetime(2000, 1, 1, 0, 0, 0)
        db.add(record)
        db.commit()

    response = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401


def test_idle_cookie_session_cannot_access_business_api(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    login(client)
    session_id = client.cookies.get(settings_module.settings.app_auth_cookie_name)
    assert session_id
    with db_session_factory() as db:
        record = db.get(AuthSessionRecord, _hash_secret(session_id))
        assert record is not None
        record.last_seen_at = datetime(2000, 1, 1, 0, 0, 0)
        record.expires_at = datetime(2999, 1, 1, 0, 0, 0)
        db.add(record)
        db.commit()

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
    assert client.get("/v1/auth/me").json() == {
        "authenticated": False,
        "expires_at": None,
        "history_namespace": None,
        "access_key_quota": None,
    }

    login(client)
    response = client.get("/v1/auth/me")

    assert response.status_code == 200
    assert response.json()["authenticated"] is True
    assert response.json()["expires_at"].endswith("Z")


def test_auth_touch_is_throttled_to_reduce_sqlite_writes(
    tmp_path,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "app_auth_touch_interval_seconds", 60)
    engine = create_engine(
        f"sqlite:///{tmp_path / 'auth-touch.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    with Session(engine) as db:
        created = create_auth_session(db, _FakeRequest(), now=_fake_now())
        first_seen_at = db.get(
            AuthSessionRecord,
            _hash_secret(created.session_id),
        ).last_seen_at

        same_window = _get_auth_session(
            db,
            created.session_id,
            now=_fake_now() + timedelta(seconds=30),
            touch=True,
        )
        db.refresh(same_window)
        after_same_window = same_window.last_seen_at

        next_window = _get_auth_session(
            db,
            created.session_id,
            now=_fake_now() + timedelta(seconds=61),
            touch=True,
        )
        db.refresh(next_window)
        after_next_window = next_window.last_seen_at

    assert after_same_window == first_seen_at
    assert after_next_window == _fake_now() + timedelta(seconds=61)


def _fake_now() -> datetime:
    return datetime(2026, 5, 26, 12, 0, 0)


class _FakeRequest:
    headers = {"user-agent": "pytest"}
    client = SimpleNamespace(host="127.0.0.1")


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


def test_admin_login_is_rate_limited(
    client: TestClient,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "admin_auth_password", "admin-secret")
    LOGIN_FAILURES.clear()

    for _ in range(2):
        response = client.post(
            "/v1/admin/login",
            json={"password": "wrong"},
            headers={"Origin": ORIGIN},
        )
        assert response.status_code == 401
        assert response.json() == {"detail": "invalid credentials"}

    response = client.post(
        "/v1/admin/login",
        json={"password": "wrong"},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 429
    assert response.json() == {"detail": "too many login attempts"}

    # User login failures use a separate bucket from admin.
    user_response = client.post(
        "/v1/auth/login",
        json={"password": "wrong"},
        headers={"Origin": ORIGIN},
    )
    assert user_response.status_code == 401


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


def test_responses_api_can_use_separate_compat_token(
    client: TestClient,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "app_compat_api_key", "compat-secret")

    response = client.post(
        "/v1/responses",
        json={"model": "test", "input": ""},
        headers={"Authorization": "Bearer compat-secret"},
    )

    assert response.status_code == 422


def test_auth_is_disabled_without_password(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    assert client.get("/v1/auth/me").json() == {
        "authenticated": True,
        "expires_at": None,
        "history_namespace": None,
        "access_key_quota": None,
    }


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


def test_login_audit_records_cloudflare_ip_for_user_success_and_failure(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    success = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={
            "Origin": ORIGIN,
            "CF-Connecting-IP": "203.0.113.9",
            "X-Forwarded-For": "198.51.100.7, 10.0.0.1",
            "CF-Ray": "abc123-SJC",
            "CF-IPCountry": "US",
        },
    )
    assert success.status_code == 200
    client.post("/v1/auth/logout", headers={"Origin": ORIGIN})

    failure = client.post(
        "/v1/auth/login",
        json={"password": "wrong"},
        headers={
            "Origin": ORIGIN,
            "CF-Connecting-IP": "203.0.113.9",
            "X-Forwarded-For": "198.51.100.7, 10.0.0.1",
        },
    )
    assert failure.status_code == 401

    from app.db.models import AuthLoginEventRecord

    with db_session_factory() as db:
        events = db.query(AuthLoginEventRecord).order_by(AuthLoginEventRecord.id).all()

    assert [(event.outcome, event.client_ip) for event in events] == [
        ("success", "203.0.113.9"),
        ("failure", "203.0.113.9"),
    ]
    assert events[0].client_ip_source == "cf-connecting-ip"
    assert events[0].cf_ray == "abc123-SJC"
    assert events[0].cf_country == "US"
    assert events[1].failure_reason == "invalid_credentials"


def test_login_audit_ip_resolution_uses_rightmost_xff_when_trusted(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", True)
    response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={
            "Origin": ORIGIN,
            "X-Forwarded-For": "198.51.100.77, 10.0.0.2",
            "X-Real-IP": "192.0.2.55",
        },
    )
    assert response.status_code == 200

    from app.db.models import AuthLoginEventRecord

    with db_session_factory() as db:
        [event] = db.query(AuthLoginEventRecord).all()

    # Rightmost hop is the one appended by the trusted proxy.
    assert event.client_ip == "10.0.0.2"
    assert event.client_ip_source == "x-forwarded-for"


def test_login_audit_ignores_xff_when_trust_disabled(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "trust_x_forwarded_for", False)
    response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={
            "Origin": ORIGIN,
            "X-Forwarded-For": "198.51.100.77, 10.0.0.2",
            "X-Real-IP": "192.0.2.55",
        },
    )
    assert response.status_code == 200

    from app.db.models import AuthLoginEventRecord

    with db_session_factory() as db:
        [event] = db.query(AuthLoginEventRecord).all()

    assert event.client_ip == "testclient"
    assert event.client_ip_source == "direct"
