"""PR-B3 / PR-B5: tombstone sticky, gate understanding, terminal phase."""

from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.db.session import get_db
from app.domain.runtime import build_initial_gate_status
from app.main import app
from app.repositories.document_repo import DocumentRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.workers.parse_worker import ParseWorker


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
        f"sqlite:///{tmp_path / 'material-lifecycle.sqlite3'}",
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


def test_delete_during_queued_parse_remains_tombstoned(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "passport_bio.pdf",
                build_pdf_bytes("Full Name: Chen Wei\nPassport Number: E12345678"),
                "application/pdf",
            )
        },
    )
    assert upload_response.status_code == 202
    document_id = upload_response.json()["document_id"]
    job_id = upload_response.json()["job_id"]

    delete_response = client.delete(f"/v1/sessions/{session_id}/files/{document_id}")
    assert delete_response.status_code == 200
    assert delete_response.json()["document_status"] == "tombstoned"

    with db_session_factory() as db:
        job = db.get(JobRecord, job_id)
        assert job is not None
        assert job.status == "cancelled"

        # Worker must not revive the document even if a stale claim raced.
        processed = ParseWorker(db).run_once()
        assert processed is False

        document = db.get(DocumentRecord, document_id)
        assert document is not None
        assert document.status == "tombstoned"
        assert DocumentRepository.is_document_tombstoned(document)


def test_documents_list_endpoint_smoke(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "i20.pdf",
                build_pdf_bytes("SEVIS ID: N1234567890\nSchool Name: NYU"),
                "application/pdf",
            )
        },
    )
    assert upload_response.status_code == 202
    document_id = upload_response.json()["document_id"]

    for path in (
        f"/v1/sessions/{session_id}/documents",
        f"/v1/sessions/{session_id}/files",
    ):
        response = client.get(path)
        assert response.status_code == 200
        payload = response.json()
        assert payload["session_id"] == session_id
        assert payload["count"] == 1
        doc = payload["documents"][0]
        assert doc["document_id"] == document_id
        assert doc["filename"] == "i20.pdf"
        assert doc["status"] == "uploaded"
        assert doc["understanding_status"] == "queued"
        assert doc["content_url"] == (
            f"/v1/sessions/{session_id}/files/{document_id}/content"
        )
        assert doc["tombstoned"] is False


def test_refuse_then_parse_keeps_phase_closed(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "session_closed"
        record.current_governor_decision = "simulated_refusal"
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="refused",
            required_documents=["passport_bio"],
        )
        db.add(record)
        db.commit()

    # Direct document+job seed simulates a late-finishing parse after refusal.
    with db_session_factory() as db:
        db.add(
            DocumentRecord(
                document_id="doc-late-parse",
                session_id=session_id,
                filename="passport_bio.pdf",
                status="parsed",
                artifact_json={
                    "status": "parsed",
                    "document_type": "passport_bio",
                    "understanding_status": "completed",
                },
            )
        )
        db.commit()
        GateRuntimeService(db).refresh_session(session_id, save=True)
        db.commit()

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "session_closed"
        assert record.current_governor_decision == "simulated_refusal"

    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Can we continue?"},
    )
    assert message_response.status_code == 409


def test_get_evidence_excerpt_returns_none_for_tombstoned_document(
    db_session_factory,
) -> None:
    from app.db.evidence_models import EvidenceItemRecord
    from app.services.evidence_service import EvidenceService

    with db_session_factory() as db:
        db.add(SessionRecord(session_id="sess-evi", declared_family="f1"))
        db.add(
            DocumentRecord(
                document_id="doc-evi",
                session_id="sess-evi",
                filename="funding.pdf",
                status="tombstoned",
                artifact_json={
                    "case_memory_tombstone": {
                        "status": "tombstoned",
                        "reason": "test",
                    }
                },
            )
        )
        db.add(
            EvidenceItemRecord(
                evidence_id="evi-tomb",
                session_id="sess-evi",
                document_id="doc-evi",
                chunk_id="chunk-1",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                value="parents",
                excerpt="Parent sponsor bank statement",
                confidence=1.0,
                metadata_json={},
            )
        )
        db.commit()

        assert EvidenceService(db).get_evidence_excerpt("evi-tomb") is None


def test_claim_next_job_skips_tombstoned_document(db_session_factory) -> None:
    with db_session_factory() as db:
        db.add(SessionRecord(session_id="sess-1", declared_family="f1"))
        db.add(
            DocumentRecord(
                document_id="doc-tomb",
                session_id="sess-1",
                filename="dead.pdf",
                status="tombstoned",
                artifact_json={
                    "case_memory_tombstone": {
                        "status": "tombstoned",
                        "reason": "test",
                    }
                },
            )
        )
        db.add(
            DocumentRecord(
                document_id="doc-live",
                session_id="sess-1",
                filename="live.pdf",
                status="uploaded",
                artifact_json={},
            )
        )
        db.add(
            JobRecord(
                job_id="job-0001-tomb",
                session_id="sess-1",
                kind="case_understanding",
                status="queued",
                payload_json={"document_id": "doc-tomb"},
            )
        )
        db.add(
            JobRecord(
                job_id="job-0002-live",
                session_id="sess-1",
                kind="case_understanding",
                status="queued",
                payload_json={"document_id": "doc-live"},
            )
        )
        db.commit()

        repo = DocumentRepository(db)
        claimed = repo.claim_next_job("case_understanding")
        assert claimed is not None
        assert claimed.payload_json["document_id"] == "doc-live"
        assert claimed.status == "processing"

        tomb_job = db.get(JobRecord, "job-0001-tomb")
        assert tomb_job is not None
        assert tomb_job.status == "cancelled"
