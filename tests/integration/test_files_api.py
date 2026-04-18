from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.db.session import get_db
from app.main import app


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'files-api.sqlite3'}",
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
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def seed_session(db_session_factory, session_id: str) -> None:
    with db_session_factory() as db:
        db.add(SessionRecord(session_id=session_id, declared_family="f1"))
        db.commit()


def test_upload_file_creates_document_and_job(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = "sess-existing"
    raw_bytes = build_pdf_bytes("SEVIS ID: N1234567890")
    seed_session(db_session_factory, session_id)

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={"file": ("i20.pdf", raw_bytes, "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_status"] == "uploaded"
    assert payload["job_status"] == "queued"

    with db_session_factory() as db:
        document = db.scalar(
            select(DocumentRecord).where(
                DocumentRecord.document_id == payload["document_id"],
            ),
        )
        job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == payload["job_id"]),
        )

        assert document is not None
        assert document.session_id == session_id
        assert document.filename == "i20.pdf"
        assert document.raw_bytes == raw_bytes
        assert document.raw_text == ""
        assert document.status == "uploaded"

        assert job is not None
        assert job.kind == "gate_parse"
        assert job.status == "queued"
        assert job.payload_json["document_id"] == document.document_id


def test_upload_file_rejects_missing_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/sessions/sess-missing/files",
        files={"file": ("passport_bio.pdf", build_pdf_bytes("US visitor"), "application/pdf")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: sess-missing"

    with db_session_factory() as db:
        document_count = db.scalar(select(func.count()).select_from(DocumentRecord))
        job_count = db.scalar(select(func.count()).select_from(JobRecord))

        assert document_count == 0
        assert job_count == 0


def test_upload_file_rejects_payload_over_64mb(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    seed_session(db_session_factory, "sess-existing")
    monkeypatch.setattr("app.services.file_service.MAX_UPLOAD_SIZE_BYTES", 4)

    response = client.post(
        "/v1/sessions/sess-existing/files",
        files={"file": ("large.pdf", b"12345", "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Uploaded file exceeds 64MB limit"


def test_upload_file_rejects_unsupported_media_type(
    client: TestClient,
    db_session_factory,
) -> None:
    seed_session(db_session_factory, "sess-existing")

    response = client.post(
        "/v1/sessions/sess-existing/files",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Only PDF and PNG/JPG/JPEG images are supported"


def test_upload_file_returns_feedback_message_for_document_type(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    seed_session(db_session_factory, "sess-existing")

    def fake_extract(self, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        fake_extract,
    )

    response = client.post(
        "/v1/sessions/sess-existing/files",
        data={"document_type": "passport_bio"},
        files={"file": ("passport_bio.pdf", build_pdf_bytes("Passport page"), "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_type"] == "passport_bio"
    assert "passport_bio" in payload["feedback_message"]
