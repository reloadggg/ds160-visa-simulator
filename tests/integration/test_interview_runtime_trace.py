from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.main import app
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


def _prepare_ready_for_interview_session(
    client: TestClient,
    db_session_factory,
) -> str:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]
    assert session_resp.status_code == 201
    for filename, raw_bytes in [
        ("ds160.pdf", build_pdf_bytes("Completed DS-160 form draft")),
        ("passport_bio.pdf", build_pdf_bytes("Passport biographic page")),
        ("i20.pdf", build_pdf_bytes("Form I-20 issued by school")),
        ("admission_letter.pdf", build_pdf_bytes("University admission letter")),
        ("funding_proof.pdf", build_pdf_bytes("Parent sponsor bank statement for tuition")),
    ]:
        upload_response = client.post(
            f"/v1/sessions/{session_id}/files",
            files={"file": (filename, raw_bytes, "application/pdf")},
        )
        assert upload_response.status_code == 202

    with db_session_factory() as db:
        while ParseWorker(db).run_once():
            pass

    return session_id


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'interview-runtime-trace.sqlite3'}",
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


def test_interview_runtime_trace_and_histories_append_per_turn(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = _prepare_ready_for_interview_session(client, db_session_factory)

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert first.status_code == 200
    assert second.status_code == 200

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert record is not None
    assert first.json()["assistant_message"]
    assert second.json()["assistant_message"]
    assert first.json()["governor_decision"] == "continue_interview"
    assert second.json()["governor_decision"] == "continue_interview"
    assert [(turn.turn_index, turn.role) for turn in turns] == [
        (1, "user"),
        (2, "assistant"),
        (3, "user"),
        (4, "assistant"),
    ]
    assert len(record.runtime_trace_json) == 14
    assert len(record.score_history_json) == 2
    assert len(record.governor_history_json) == 2
    assert [entry["node_name"] for entry in record.runtime_trace_json[:7]] == [
        "receive_input",
        "extract_claims",
        "resolve_evidence",
        "consistency_check",
        "score_case",
        "governor_decide",
        "turn_decision",
    ]
    assert [entry["node_name"] for entry in record.runtime_trace_json[7:]] == [
        "receive_input",
        "extract_claims",
        "resolve_evidence",
        "consistency_check",
        "score_case",
        "governor_decide",
        "turn_decision",
    ]
    assert record.score_history_json[0]["scoring_stage"] == "interview_turn"
    assert {
        "category_fit",
        "document_readiness",
        "narrative_consistency",
        "confidence",
        "missing_evidence",
        "risk_flags",
        "summary",
    } <= set(record.score_history_json[0].keys())
    assert {
        "decision",
        "summary",
    } <= set(record.governor_history_json[0].keys())
