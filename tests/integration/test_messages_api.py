from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.db.session import get_db
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.domain.runtime import build_initial_gate_status
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


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'messages-api.sqlite3'}",
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


def seed_ready_for_interview_session(
    client: TestClient,
    db_session_factory,
) -> str:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]
    assert session_resp.status_code == 201

    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-1"],
        source_summary="document evidence",
    )

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.profile_json = profile.model_dump(mode="json")
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="parent_sponsored",
            required_documents=[
                "ds160",
                "passport_bio",
                "i20",
                "admission_letter",
                "funding_proof",
            ],
        )
        for document_id, filename in [
            ("doc-1", "ds160.txt"),
            ("doc-2", "passport_bio.txt"),
            ("doc-3", "i20.txt"),
            ("doc-4", "admission_letter.txt"),
            ("doc-5", "funding_proof.txt"),
        ]:
            db.add(
                DocumentRecord(
                    document_id=document_id,
                    session_id=session_id,
                    filename=filename,
                    status="parsed",
                    artifact_json={
                        "status": "parsed",
                        "filename": filename,
                        "source_type": "text",
                    },
                )
            )
        db.add(
            EvidenceItemRecord(
                evidence_id="evi-1",
                session_id=session_id,
                document_id="doc-5",
                chunk_id="chunk-1",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                value="parents",
                excerpt="Parent sponsor bank statement",
                confidence=1.0,
                metadata_json={},
            )
        )
        db.add(record)
        db.commit()

    return session_id


def test_message_turn_short_circuits_to_gate_when_gate_not_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.ExtractorService.apply_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("gate 未通过前不应进入正式 interview runtime")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert (
        payload["assistant_message"]
        == "当前处于材料门控阶段。请先补齐必需材料，之后才能进入正式 interview。"
    )
    assert payload["gate_progress"] == {
        "overall_status": "pending_documents",
        "ready_count": 0,
        "uploaded_count": 0,
        "missing_count": 5,
        "documents": [
            {
                "document_type": "ds160",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "passport_bio",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "i20",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "admission_letter",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "funding_proof",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
        ],
    }

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "gate_review"
        assert record.gate_status_json["status"] == "pending_documents"


def test_message_turn_uses_question_agent_output_for_continue_interview(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "assistant_message": "What is the purpose of your travel?",
                    "requested_documents": [],
                    "decision_hint": "continue_interview",
                },
            ),
            {"model": "gpt-5.4"},
        )
        if module_key == "question_agent"
        else (None, {"model": None}),
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService._fallback_question_action",
        lambda self, governor_decision, score: (_ for _ in ()).throw(
            AssertionError("question agent path should not fall back")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["requested_documents"] == []

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert [entry["node_name"] for entry in record.runtime_trace_json] == [
            "receive_input",
            "extract_claims",
            "resolve_evidence",
            "consistency_check",
            "score_case",
            "governor_decide",
            "build_next_action",
        ]
        assert record.score_history_json[-1]["scoring_stage"] == "interview_turn"
        assert record.governor_history_json[-1]["decision"] == "continue_interview"


def test_message_turn_falls_back_when_question_agent_errors(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            object(),
            {"model": "gpt-5.4"},
        )
        if module_key == "question_agent"
        else (None, {"model": None}),
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.QuestionAgentRunner.run",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("runtime failure")),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["requested_documents"] == []


def test_message_turn_rejects_non_user_role(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 422


def test_funding_proof_upload_allows_interview_to_continue(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.ExtractorService.apply_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("worker 完成前不应进入正式 interview runtime")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
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
    pre_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert pre_worker_response.status_code == 200
    pre_worker_payload = pre_worker_response.json()
    assert pre_worker_payload["governor_decision"] == "need_more_evidence"
    assert (
        pre_worker_payload["assistant_message"]
        == "当前处于材料门控阶段。材料已提交，系统正在解析，暂时还不能进入正式 interview。"
    )
    assert pre_worker_payload["requested_documents"] == [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
    ]
    assert pre_worker_payload["gate_progress"] == {
        "overall_status": "waiting_for_parse",
        "ready_count": 0,
        "uploaded_count": 1,
        "missing_count": 4,
        "documents": [
            {
                "document_type": "ds160",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "passport_bio",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "i20",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "admission_letter",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "funding_proof",
                "status": "uploaded",
                "is_uploaded": True,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
        ],
    }

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    monkeypatch.undo()

    post_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert post_worker_response.status_code == 200
    payload = post_worker_response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert (
        payload["assistant_message"]
        == "当前处于材料门控阶段。请先补齐必需材料，之后才能进入正式 interview。"
    )
    assert sorted(payload["requested_documents"]) == sorted(
        ["ds160", "passport_bio", "i20", "admission_letter"]
    )


def test_uploaded_document_type_metadata_advances_gate_for_funding_proof(
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
            scenario_key="document_type_metadata",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "bank-statement-final.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )

    assert upload_response.status_code == 202
    assert upload_response.json()["document_type"] == "funding_proof"

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "interview"
        assert record.gate_status_json["status"] == "ready_for_interview"


def test_gate_progress_reports_ready_uploaded_and_missing_mix(
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
            scenario_key="mixed_progress",
            required_documents=["ds160", "passport_bio", "funding_proof"],
        )
        db.add(
            DocumentRecord(
                document_id="doc-ready",
                session_id=session_id,
                filename="ds160.txt",
                status="parsed",
                artifact_json={
                    "status": "parsed",
                    "filename": "ds160.txt",
                    "source_type": "text",
                },
            )
        )
        db.add(
            DocumentRecord(
                document_id="doc-uploaded",
                session_id=session_id,
                filename="passport_bio.txt",
                status="uploaded",
                artifact_json={
                    "status": "uploaded",
                    "filename": "passport_bio.txt",
                },
            )
        )
        db.add(record)
        db.commit()

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Checking current gate progress."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert payload["gate_progress"] == {
        "overall_status": "waiting_for_parse",
        "ready_count": 1,
        "uploaded_count": 1,
        "missing_count": 1,
        "documents": [
            {
                "document_type": "ds160",
                "status": "ready",
                "is_uploaded": True,
                "is_parsed": True,
                "meets_minimum_fields": True,
            },
            {
                "document_type": "passport_bio",
                "status": "uploaded",
                "is_uploaded": True,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
            {
                "document_type": "funding_proof",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            },
        ],
    }


def test_confirmed_fraud_message_triggers_simulated_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I lied on my DS-160 and used fake bank statements.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "simulated_refusal"


def test_negated_fraud_statement_does_not_trigger_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I did not use fake bank statements.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
