from collections.abc import Generator
import asyncio

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.domain.runtime import build_initial_gate_status
from app.main import app
from app.agents.schemas import InterviewNextAction
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
        f"sqlite:///{tmp_path / 'parse-worker.sqlite3'}",
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


def test_parse_worker_processes_uploaded_document_before_next_message(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message=(
                "Please upload funding proof."
                if score.missing_evidence
                else "What is the purpose of your travel?"
            ),
            requested_documents=list(score.missing_evidence[:1]),
            decision=(
                "need_more_evidence"
                if score.missing_evidence
                else "continue_interview"
            ),
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="runtime_test",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    first_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )
    assert upload_response.status_code == 202
    upload_payload = upload_response.json()
    assert upload_payload["understanding_status"] == "queued"
    assert upload_payload["case_board_delta"]["latest_material"][
        "understanding_status"
    ] == "queued"

    assert first_response.status_code == 200
    assert first_response.json()["governor_decision"] in {
        "continue_interview",
        "need_more_evidence",
    }

    pre_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert pre_worker_response.status_code == 200
    assert pre_worker_response.json()["gate_progress"]["overall_status"] == (
        "pending_documents"
    )

    with db_session_factory() as db:
        while ParseWorker(db).run_once():
            pass

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        document = db.get(DocumentRecord, upload_response.json()["document_id"])
        assert record is not None
        assert document is not None
        assert record.phase_state == "interview"
        assert record.gate_status_json["status"] == "pending_documents"
        assert record.current_governor_decision == "continue_interview"
        assert record.interviewer_state_json["decision"] == "continue_interview"
        assert record.current_focus_json == {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        }
        assert document.artifact_json["understanding_status"] == "completed"
        assert "material_understanding_result" in document.artifact_json
        assert document.artifact_json["case_board_delta"]["claims"][0]["field_path"] == (
            "/funding/primary_source"
        )

    post_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert post_worker_response.status_code == 200
    assert post_worker_response.json()["governor_decision"] == "continue_interview"


def test_parse_worker_claims_oldest_queued_job_first(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="runtime_test",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    first_upload = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof_1.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )
    second_upload = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof_2.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )

    assert first_upload.status_code == 202
    assert second_upload.status_code == 202

    first_job_id = first_upload.json()["job_id"]
    second_job_id = second_upload.json()["job_id"]

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    with db_session_factory() as db:
        first_job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == first_job_id),
        )
        second_job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == second_job_id),
        )

        assert first_job is not None
        assert second_job is not None
        assert first_job.job_id < second_job.job_id
        assert first_job.status == "completed"
        assert second_job.status == "queued"


def test_parse_worker_material_refresh_updates_graph_state_without_assistant_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")

    def legacy_refresh_must_not_run(self, record, *, reason: str) -> dict:
        raise AssertionError("legacy material refresh should not run in graph mode")

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.refresh_after_material_change",
        legacy_refresh_must_not_run,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="runtime_test",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )

    assert upload_response.status_code == 202
    job_id = upload_response.json()["job_id"]

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    with db_session_factory() as db:
        job = db.get(JobRecord, job_id)
        assistant_count = db.scalar(
            select(func.count())
            .select_from(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
        )
        record = db.get(SessionRecord, session_id)

    assert job is not None
    assert job.status == "completed"
    assert assistant_count == 0
    assert record is not None
    material_refresh = record.interviewer_state_json["last_material_refresh"]
    assert material_refresh["agent_runtime"] == "graph"
    assert material_refresh["selected_public_runtime"] == "graph"
    assert material_refresh["prompt_trace"]["graph_trigger"] == "material_change"
    assert material_refresh["prompt_trace"]["material_change_reason"] == "case_understanding"
    assert material_refresh["assistant_turn_created"] is False


def test_parse_worker_keeps_completed_job_when_material_refresh_fails(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", False)
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.GraphRuntimeAdapter.run_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            RuntimeError("graph material refresh failed after parse")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="runtime_test",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )

    assert upload_response.status_code == 202
    job_id = upload_response.json()["job_id"]

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    with db_session_factory() as db:
        job = db.get(JobRecord, job_id)
        assistant_count = db.scalar(
            select(func.count())
            .select_from(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
        )

    assert job is not None
    assert job.status == "completed"
    assert assistant_count == 0
