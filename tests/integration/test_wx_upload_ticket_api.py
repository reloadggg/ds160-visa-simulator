from collections.abc import Generator
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import (
    AccessKeySessionRecord,
    DocumentRecord,
    SessionRecord,
    WxUploadTicketRecord,
)
from app.db.session import get_db
from app.main import app
from app.services.access_key_service import AccessKeyService
from app.services.wx_upload_ticket_service import hash_ticket


ORIGIN = "http://testserver"


def build_pdf_bytes(text: str = "SEVIS ID: N1234567890") -> bytes:
    pdf = fitz.open()
    page = pdf.new_page()
    page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'wx-upload-ticket.sqlite3'}",
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
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "fallback-password")
    monkeypatch.setattr(
        settings_module.settings,
        "app_auth_password_user_fallback_enabled",
        False,
    )
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", True)
    monkeypatch.setattr(settings_module.settings, "app_auth_session_ttl_seconds", 3600)
    monkeypatch.setattr(settings_module.settings, "app_auth_idle_timeout_seconds", 3600)


def seed_access_key_session(
    db_session_factory,
    *,
    session_id: str = "sess-wx-owned",
) -> str:
    with db_session_factory() as db:
        created = AccessKeyService(db).create_key(label="wx mvp", usage_limit=5)
        db.add(SessionRecord(session_id=session_id, declared_family="f1"))
        db.add(
            AccessKeySessionRecord(
                key_id=created.record.key_id,
                session_id=session_id,
            )
        )
        db.commit()
        return created.plaintext_key


def login_with_key(client: TestClient, access_key: str) -> None:
    response = client.post(
        "/v1/auth/login",
        json={"password": access_key},
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200


def create_ticket(client: TestClient, session_id: str = "sess-wx-owned") -> str:
    response = client.post(
        f"/v1/sessions/{session_id}/upload-ticket",
        headers={"Origin": ORIGIN},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ticket"].startswith("wxup_")
    assert payload["session_id"] == session_id
    return payload["ticket"]


def test_create_ticket_requires_cookie_auth_when_auth_enabled(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    seed_access_key_session(db_session_factory)

    response = client.post(
        "/v1/sessions/sess-wx-owned/upload-ticket",
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 401


def test_create_ticket_for_owned_access_key_session(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)

    response = client.post(
        "/v1/sessions/sess-wx-owned/upload-ticket",
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == "sess-wx-owned"
    assert payload["max_files"] == 5
    assert payload["uploaded_count"] == 0
    assert payload["remaining_files"] == 5
    assert payload["status"] == "active"
    with db_session_factory() as db:
        assert db.get(WxUploadTicketRecord, hash_ticket(payload["ticket"])) is not None


def test_create_ticket_rejects_cross_key_session(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory, session_id="sess-owned")
    with db_session_factory() as db:
        db.add(SessionRecord(session_id="sess-other", declared_family="f1"))
        db.commit()
    login_with_key(client, access_key)

    response = client.post(
        "/v1/sessions/sess-other/upload-ticket",
        headers={"Origin": ORIGIN},
    )

    assert response.status_code == 403


def test_ticket_upload_is_public_but_requires_valid_ticket(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)
    ticket = create_ticket(client)

    client.cookies.clear()
    response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("i20.pdf", build_pdf_bytes(), "application/pdf")},
        data={"session_id": "sess-wx-owned", "context_text": "I-20 from WeChat"},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["session_id"] == "sess-wx-owned"
    assert payload["uploaded_count"] == 1
    assert payload["remaining_files"] == 4
    assert payload["upload"]["document_status"] == "uploaded"
    assert payload["upload_results"][0]["document_id"] == payload["upload"]["document_id"]
    with db_session_factory() as db:
        document = db.get(DocumentRecord, payload["upload"]["document_id"])
        assert document is not None
        assert document.filename == "i20.pdf"


def test_ticket_status_returns_upload_results(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)
    ticket = create_ticket(client)
    upload_response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("passport.png", b"not-real-png-but-name-is-enough", "image/png")},
    )
    assert upload_response.status_code == 202

    response = client.get(f"/v1/wx/upload-tickets/{ticket}")

    assert response.status_code == 200
    payload = response.json()
    assert payload["uploaded_count"] == 1
    assert payload["upload_results"][0]["file_name"] == "passport.png"


def test_ticket_upload_rejects_invalid_or_mismatched_ticket(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)
    ticket = create_ticket(client)

    missing_response = client.post(
        "/v1/wx/upload-tickets/wxup_missing/files",
        files={"file": ("i20.pdf", build_pdf_bytes(), "application/pdf")},
    )
    mismatch_response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("i20.pdf", build_pdf_bytes(), "application/pdf")},
        data={"session_id": "sess-other"},
    )

    assert missing_response.status_code == 404
    assert mismatch_response.status_code == 403


def test_ticket_upload_rejects_expired_ticket(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)
    ticket = create_ticket(client)
    with db_session_factory() as db:
        record = db.get(WxUploadTicketRecord, hash_ticket(ticket))
        assert record is not None
        record.expires_at = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(
            seconds=1
        )
        db.add(record)
        db.commit()

    response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("i20.pdf", build_pdf_bytes(), "application/pdf")},
    )

    assert response.status_code == 410


def test_ticket_upload_rejects_when_file_limit_reached(
    client: TestClient,
    db_session_factory,
    enabled_auth: None,
) -> None:
    access_key = seed_access_key_session(db_session_factory)
    login_with_key(client, access_key)
    ticket = create_ticket(client)
    with db_session_factory() as db:
        record = db.get(WxUploadTicketRecord, hash_ticket(ticket))
        assert record is not None
        record.max_files = 1
        db.add(record)
        db.commit()

    first_response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("first.pdf", build_pdf_bytes("first"), "application/pdf")},
    )
    second_response = client.post(
        f"/v1/wx/upload-tickets/{ticket}/files",
        files={"file": ("second.pdf", build_pdf_bytes("second"), "application/pdf")},
    )

    assert first_response.status_code == 202
    assert second_response.status_code == 409
