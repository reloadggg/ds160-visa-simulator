from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-demo-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "admin-pass")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", False)

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


def test_admin_key_quota_and_session_ownership_flow(client: TestClient) -> None:
    login_response = client.post("/v1/admin/login", json={"password": "admin-pass"})
    assert login_response.status_code == 200

    created = client.post(
        "/v1/admin/access-keys",
        json={"label": "demo customer", "usage_limit": 1},
    )
    assert created.status_code == 200
    key_payload = created.json()
    plaintext_key = key_payload["key"]
    key_id = key_payload["record"]["key_id"]
    assert plaintext_key.startswith(f"ds160_{key_id}_")
    assert key_payload["record"]["usage_count"] == 0

    client.post("/v1/admin/logout")
    user_login = client.post("/v1/auth/login", json={"password": plaintext_key})
    assert user_login.status_code == 200
    assert user_login.json()["history_namespace"] == f"key_{key_id}"

    first_session = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert first_session.status_code == 201
    session_id = first_session.json()["session_id"]

    second_session = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert second_session.status_code == 403
    assert "quota exhausted" in second_session.json()["detail"]

    session_list = client.get("/v1/sessions")
    assert session_list.status_code == 200
    assert [item["session_id"] for item in session_list.json()["sessions"]] == [
        session_id
    ]

    client.post("/v1/auth/logout")
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    keys = client.get("/v1/admin/access-keys")
    assert keys.status_code == 200
    [record] = keys.json()["keys"]
    assert record["usage_count"] == 1
    assert record["remaining_uses"] == 0

    disabled = client.patch(
        f"/v1/admin/access-keys/{key_id}",
        json={"enabled": False},
    )
    assert disabled.status_code == 200
    assert disabled.json()["record"]["enabled"] is False


def test_user_model_config_requires_admin_toggle(
    client: TestClient,
) -> None:
    config = client.get("/v1/app-config").json()
    assert config["user_model_config_enabled"] is False

    client.post("/v1/admin/login", json={"password": "admin-pass"})
    updated = client.patch(
        "/v1/admin/settings",
        json={"user_model_config_enabled": True},
    )
    assert updated.status_code == 200
    assert client.get("/v1/app-config").json()["user_model_config_enabled"] is True


def test_access_key_cannot_read_another_key_session(client: TestClient) -> None:
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    first_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "first", "usage_limit": 1},
    ).json()["key"]
    second_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "second", "usage_limit": 1},
    ).json()["key"]

    client.post("/v1/admin/logout")
    assert (
        client.post("/v1/auth/login", json={"password": first_key}).status_code
        == 200
    )
    session_id = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    client.post("/v1/auth/logout")
    assert (
        client.post("/v1/auth/login", json={"password": second_key}).status_code
        == 200
    )
    response = client.get(f"/v1/sessions/{session_id}/messages")

    assert response.status_code == 403
    assert "not available" in response.json()["detail"]


def test_terminal_session_rejects_more_messages_and_uploads(
    client: TestClient,
    db_session_factory,
) -> None:
    client.post("/v1/admin/login", json={"password": "admin-pass"})
    access_key = client.post(
        "/v1/admin/access-keys",
        json={"label": "terminal", "usage_limit": 1},
    ).json()["key"]

    client.post("/v1/admin/logout")
    client.post("/v1/auth/login", json={"password": access_key})
    session_id = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
    ).json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "completed"
        db.add(record)
        db.commit()

    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Can I continue?"},
    )
    assert message_response.status_code == 409
    assert "已结束" in message_response.json()["detail"]

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={"file": ("i20.txt", b"SEVIS ID: N1234567890", "text/plain")},
    )
    assert upload_response.status_code == 409
    assert "已结束" in upload_response.json()["detail"]
