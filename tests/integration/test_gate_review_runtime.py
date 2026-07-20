from collections.abc import Generator
import asyncio

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app
from app.workers.parse_worker import ParseWorker
from app.workers.parse_worker import stop_parse_worker_runtime


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
        f"sqlite:///{tmp_path / 'gate-review-runtime.sqlite3'}",
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
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    asyncio.run(stop_parse_worker_runtime(app))
    monkeypatch.setenv("PARSE_WORKER_INLINE", "0")
    app.state.parse_worker_session_factory = None
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(stop_parse_worker_runtime(app))
    app.dependency_overrides.clear()
    app.state.parse_worker_session_factory = None


def test_f1_gate_review_runtime_progresses_from_pending_to_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Offline suite has no multimodal model; allow parsed documents to satisfy
    # gate readiness without completed understanding (production default is True).
    monkeypatch.setattr(settings_module.settings, "material_understanding_required", False)
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        created = db.get(SessionRecord, session_id)
        assert created is not None
        assert created.phase_state == "intake"
        assert created.gate_status_json["status"] == "pending_documents"

    for document_type, filename, raw_bytes in [
        ("ds160", "ds160.pdf", build_pdf_bytes("Completed DS-160 form draft")),
        ("passport_bio", "passport_bio.pdf", build_pdf_bytes("Passport biographic page")),
        ("i20", "i20.pdf", build_pdf_bytes("Form I-20 issued by school")),
        ("admission_letter", "admission_letter.pdf", build_pdf_bytes("University admission letter")),
        ("funding_proof", "funding_proof.pdf", build_pdf_bytes("Parent sponsor bank statement for tuition")),
    ]:
        upload_response = client.post(
            f"/v1/sessions/{session_id}/files",
            data={"document_type": document_type},
            files={"file": (filename, raw_bytes, "application/pdf")},
        )
        assert upload_response.status_code == 202

    with db_session_factory() as db:
        waiting = db.get(SessionRecord, session_id)
        assert waiting is not None
        assert waiting.phase_state == "interview"
        assert waiting.gate_status_json["status"] == "waiting_for_parse"

    with db_session_factory() as db:
        while ParseWorker(db).run_once():
            pass

    with db_session_factory() as db:
        ready = db.get(SessionRecord, session_id)
        assert ready is not None
        assert ready.phase_state == "interview"
        assert ready.gate_status_json["status"] == "ready_for_interview"
