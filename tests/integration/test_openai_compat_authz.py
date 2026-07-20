"""PR-B4: OpenAI-compat writers enforce session ownership and quota."""

from __future__ import annotations

from collections.abc import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.core.simple_auth import LOGIN_FAILURES
from app.db.base import Base
from app.db.models import AccessKeyRecord, AccessKeySessionRecord, SessionRecord
from app.db.session import get_db
from app.main import app
from app.services.access_key_service import AccessKeyService
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput


ORIGIN = "http://testserver"


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'openai-compat-authz.sqlite3'}",
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


@pytest.fixture()
def enabled_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    LOGIN_FAILURES.clear()
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "fallback-password")
    monkeypatch.setattr(
        settings_module.settings,
        "app_auth_password_user_fallback_enabled",
        False,
    )
    monkeypatch.setattr(settings_module.settings, "admin_auth_password", "admin-pass")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", True)
    monkeypatch.setattr(settings_module.settings, "app_auth_session_ttl_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_idle_timeout_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_compat_api_key", None)
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService._build_runtime",
        lambda self, declared_family: {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        },
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAIAgentsInterviewerRunner.run",
        lambda self, **kwargs: NativeInterviewerOutput(
            assistant_message="Please continue.",
            decision="continue_interview",
        ),
    )


def _admin_create_key(client: TestClient, *, usage_limit: int = 2) -> str:
    assert client.post(
        "/v1/admin/login",
        json={"password": "admin-pass"},
        headers={"Origin": ORIGIN},
    ).status_code == 200
    created = client.post(
        "/v1/admin/access-keys",
        json={"label": "compat", "usage_limit": usage_limit},
        headers={"Origin": ORIGIN},
    )
    assert created.status_code == 200
    key = created.json()["key"]
    client.post("/v1/admin/logout", headers={"Origin": ORIGIN})
    return key


def _login_key(client: TestClient, access_key: str) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"password": access_key},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def test_chat_completions_rejects_cross_user_session(
    client: TestClient,
    enabled_auth: None,
) -> None:
    first_key = _admin_create_key(client, usage_limit=1)
    second_key = _admin_create_key(client, usage_limit=1)

    _login_key(client, first_key)
    owned = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )
    assert owned.status_code == 201
    session_id = owned.json()["session_id"]

    client.post("/v1/auth/logout", headers={"Origin": ORIGIN})
    _login_key(client, second_key)

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "hello"}],
            "metadata": {"session_id": session_id, "declared_family": "f1"},
        },
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403
    assert "not available" in response.json()["detail"]


def test_responses_rejects_cross_user_session(
    client: TestClient,
    enabled_auth: None,
) -> None:
    first_key = _admin_create_key(client, usage_limit=1)
    second_key = _admin_create_key(client, usage_limit=1)

    _login_key(client, first_key)
    owned = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )
    session_id = owned.json()["session_id"]

    client.post("/v1/auth/logout", headers={"Origin": ORIGIN})
    _login_key(client, second_key)

    response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "hello",
            "metadata": {"session_id": session_id},
        },
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 403


def test_compat_create_consumes_access_key_quota(
    client: TestClient,
    enabled_auth: None,
    db_session_factory,
) -> None:
    access_key = _admin_create_key(client, usage_limit=1)
    _login_key(client, access_key)

    first = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "first session"}],
            "metadata": {"declared_family": "f1"},
        },
        headers={"Origin": ORIGIN},
    )
    assert first.status_code == 200
    session_id = first.json()["metadata"]["session_id"]

    second = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "second session"}],
            "metadata": {"declared_family": "f1"},
        },
        headers={"Origin": ORIGIN},
    )
    assert second.status_code == 403
    assert "quota exhausted" in second.json()["detail"]

    with db_session_factory() as db:
        bindings = db.execute(select(AccessKeySessionRecord)).scalars().all()
        assert len(bindings) == 1
        assert bindings[0].session_id == session_id
        key = db.execute(select(AccessKeyRecord)).scalar_one()
        assert key.usage_count == 1


def test_machine_key_can_access_any_session(
    client: TestClient,
    enabled_auth: None,
    monkeypatch: pytest.MonkeyPatch,
    db_session_factory,
) -> None:
    monkeypatch.setattr(settings_module.settings, "app_compat_api_key", "compat-secret")
    access_key = _admin_create_key(client, usage_limit=1)
    _login_key(client, access_key)
    owned = client.post(
        "/v1/sessions",
        json={"declared_family": "f1"},
        headers={"Origin": ORIGIN},
    )
    session_id = owned.json()["session_id"]
    client.cookies.clear()

    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "machine write"}],
            "metadata": {"session_id": session_id},
        },
        headers={"Authorization": "Bearer compat-secret"},
    )
    assert response.status_code == 200
    assert response.json()["metadata"]["session_id"] == session_id

    with db_session_factory() as db:
        assert db.get(SessionRecord, session_id) is not None
