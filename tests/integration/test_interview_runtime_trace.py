from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.agents.schemas import InterviewNextAction
from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.domain.runtime import RuntimeTraceEntry
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_build_question_action(
        self,
        session_id,
        profile,
        score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ):
        trace_entries.extend(
            [
                RuntimeTraceEntry(
                    node_name="governor_decide",
                    summary=f"decision={governor_decision}",
                ),
                RuntimeTraceEntry(
                    node_name="decide_capability",
                    summary="planned=none",
                ),
                RuntimeTraceEntry(
                    node_name="resolve_capability",
                    summary="resolved=none",
                ),
                RuntimeTraceEntry(
                    node_name="turn_decision",
                    summary="decision=continue_interview",
                    turn_decision="continue_interview",
                    metadata={"boundary_decision": governor_decision},
                ),
            ]
        )
        return InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision="continue_interview",
        )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        fake_build_question_action,
    )
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
    assert first.json()["governor_decision"] == first.json()["turn_decision"]["decision"]
    assert second.json()["governor_decision"] == second.json()["turn_decision"]["decision"]
    assert [(turn.turn_index, turn.role) for turn in turns[-4:]] == [
        (turns[-4].turn_index, "user"),
        (turns[-3].turn_index, "assistant"),
        (turns[-2].turn_index, "user"),
        (turns[-1].turn_index, "assistant"),
    ]
    material_refresh_turns = [
        turn
        for turn in turns
        if (turn.metadata_json or {}).get("turn_record", {}).get("user_input", "").startswith(
            "document_parsed:"
        )
    ]
    assert material_refresh_turns
    assert len(record.runtime_trace_json) >= 18
    assert len(record.score_history_json) >= 2
    assert len(record.governor_history_json) >= 2
    trace_groups = _trace_groups(record.runtime_trace_json)
    user_turn_groups = [
        group
        for group in trace_groups
        if group and group[0].get("node_name") == "receive_input"
    ]
    material_change_groups = [
        group
        for group in trace_groups
        if group and group[0].get("node_name") == "material_changed"
    ]
    assert material_change_groups
    assert len(user_turn_groups) >= 2
    expected_user_turn_nodes = [
        "receive_input",
        "extract_claims",
        "resolve_evidence",
        "consistency_check",
        "score_case",
        "governor_decide",
        "decide_capability",
        "resolve_capability",
        "turn_decision",
    ]
    assert [entry["node_name"] for entry in user_turn_groups[-2]] == expected_user_turn_nodes
    assert [entry["node_name"] for entry in user_turn_groups[-1]] == expected_user_turn_nodes
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


def _trace_groups(runtime_trace_json: list[dict]) -> list[list[dict]]:
    groups: list[list[dict]] = []
    current_group: list[dict] = []
    for entry in runtime_trace_json:
        current_group.append(entry)
        if entry.get("node_name") == "turn_decision":
            groups.append(current_group)
            current_group = []
    if current_group:
        groups.append(current_group)
    return groups
