from collections.abc import Generator
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    RiskFlag,
    ScoreState,
    FieldState,
    FieldStateRecord,
)
from app.domain.runtime import RuntimeTraceEntry, build_initial_gate_status
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


@pytest.fixture(autouse=True)
def disable_runtime_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)


def seed_ready_for_interview_session(
    client: TestClient,
    db_session_factory,
    *,
    funding_source: str = "parents",
    documented_funding: bool = True,
) -> str:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]
    assert session_resp.status_code == 201

    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    profile.funding["primary_source"] = funding_source
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.DOCUMENTED if documented_funding else FieldState.CLAIMED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-1"] if documented_funding else [],
        source_summary="document evidence" if documented_funding else None,
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
        document_specs = [
            ("doc-1", "ds160.txt"),
            ("doc-2", "passport_bio.txt"),
            ("doc-3", "i20.txt"),
            ("doc-4", "admission_letter.txt"),
        ]
        if documented_funding:
            document_specs.append(("doc-5", "funding_proof.txt"))
        for document_id, filename in document_specs:
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
        if documented_funding:
            db.add(
                EvidenceItemRecord(
                    evidence_id="evi-1",
                    session_id=session_id,
                    document_id="doc-5",
                    chunk_id="chunk-1",
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value=funding_source,
                    excerpt=f"{funding_source.title()} sponsor bank statement",
                    confidence=1.0,
                    metadata_json={},
                )
            )
        db.add(record)
        db.commit()

    return session_id


def test_message_turn_allows_interview_runtime_when_gate_not_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    runtime_calls: list[tuple[str, str, str]] = []

    def fake_run_turn(self, record, message_text: str) -> dict:
        runtime_calls.append((record.session_id, message_text, record.phase_state))
        return {
            "assistant_message": "Why do you want to study in the U.S.?",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 65,
                "document_readiness": 20,
                "narrative_consistency": 60,
                "confidence": 55,
            },
            "requested_documents": [],
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert runtime_calls == [
        (
            session_id,
            "My parents will pay for my studies.",
            "gate_review",
        )
    ]
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "Why do you want to study in the U.S.?"
    assert payload["requested_documents"] == []
    assert payload["score_summary"] == {
        "category_fit": 65,
        "document_readiness": 20,
        "narrative_consistency": 60,
        "confidence": 55,
    }
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


def test_message_turn_keeps_family_selection_gate_before_interview_runtime(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("未选择签证家族前不应进入 interviewer runtime")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": None})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to explain my plan first."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert payload["assistant_message"] == "当前处于材料门控阶段，请先选择签证家族。"
    assert payload["requested_documents"] == []
    assert payload["gate_progress"] == {
        "overall_status": "family_not_selected",
        "ready_count": 0,
        "uploaded_count": 0,
        "missing_count": 0,
        "documents": [],
    }

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.content, turn.source) for turn in turns] == [
        (
            "user",
            "I want to explain my plan first.",
            "user_message",
        ),
        (
            "assistant",
            "当前处于材料门控阶段，请先选择签证家族。",
            "gate_runtime_service",
        ),
    ]


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
        assert record.current_focus_json == {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        }
        assert record.interviewer_state_json["owner"] == "interviewer_runtime_service"
        assert record.interviewer_state_json["next_action"] == "answer_question"


def test_message_turn_uses_turn_history_to_advance_second_question(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to study in the U.S."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["assistant_message"] == "What is the purpose of your travel?"
    assert second.json()["assistant_message"] == (
        "Which school admitted you, and why did you choose it?"
    )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert [(turn.turn_index, turn.role, turn.content) for turn in turns] == [
        (1, "user", "I want to study in the U.S."),
        (2, "assistant", "What is the purpose of your travel?"),
        (3, "user", "I will study computer science."),
        (
            4,
            "assistant",
            "Which school admitted you, and why did you choose it?",
        ),
    ]
    assert record is not None
    assert record.interviewer_state_json["history_turn_count"] == 2
    assert record.profile_json["ds160_view"]["turn_history"] == [
        {
            "turn_id": turns[0].turn_id,
            "turn_index": 1,
            "role": "user",
            "content": "I want to study in the U.S.",
            "source": "user_message",
        },
        {
            "turn_id": turns[1].turn_id,
            "turn_index": 2,
            "role": "assistant",
            "content": "What is the purpose of your travel?",
            "source": "interviewer_runtime_service",
        },
        {
            "turn_id": turns[2].turn_id,
            "turn_index": 3,
            "role": "user",
            "content": "I will study computer science.",
            "source": "user_message",
        },
    ]


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


def test_message_turn_falls_back_when_question_agent_outputs_multiple_focus_items(
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
                    "assistant_message": "Why did you choose this school?",
                    "requested_documents": ["funding_proof"],
                    "decision_hint": "need_more_evidence",
                },
            ),
            {"model": "gpt-5.4"},
        )
        if module_key == "question_agent"
        else (None, {"model": None}),
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


def test_funding_proof_upload_keeps_interview_flow_while_parse_is_pending(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "What school will you attend?",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 70,
                "document_readiness": 25,
                "narrative_consistency": 60,
                "confidence": 58,
            },
            "requested_documents": [],
        },
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.ExtractorService.apply_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("本测试应直接使用伪造的 runtime 输出")
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
    assert pre_worker_payload["governor_decision"] == "continue_interview"
    assert pre_worker_payload["assistant_message"] == "What school will you attend?"
    assert pre_worker_payload["requested_documents"] == []
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
    assert upload_response.status_code == 202


def test_irrelevant_upload_does_not_shift_gate_flow_or_primary_request(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 10,
                "narrative_consistency": 50,
                "confidence": 45,
            },
            "requested_documents": [],
        },
    )

    class IrrelevantExtractionResult:
        fields: list[object] = []

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: IrrelevantExtractionResult(),
    )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="irrelevant-upload",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "funding-proof.pdf",
                build_pdf_bytes("Tourism flyer"),
                "application/pdf",
            )
        },
    )
    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Please continue."},
    )

    assert upload_response.status_code == 202
    assert upload_response.json()["main_flow_feedback"]["status"] == "not_helpful"

    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["assistant_message"] == "当前最缺的关键证明是 funding_proof。"
    assert payload["requested_documents"] == ["funding_proof"]
    assert payload["gate_progress"] == {
        "overall_status": "pending_documents",
        "ready_count": 0,
        "uploaded_count": 0,
        "missing_count": 1,
        "documents": [
            {
                "document_type": "funding_proof",
                "status": "missing",
                "is_uploaded": False,
                "is_parsed": False,
                "meets_minimum_fields": False,
            }
        ],
    }


def test_helpful_secondary_upload_enters_gate_flow_without_hijacking_primary_focus(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 20,
                "narrative_consistency": 55,
                "confidence": 50,
            },
            "requested_documents": [],
        },
    )

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="secondary-helpful-upload",
            required_documents=["ds160", "funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "funding-proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
        },
    )
    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Please continue."},
    )

    assert upload_response.status_code == 202
    assert upload_response.json()["main_flow_feedback"]["status"] == "partial_helpful"

    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["assistant_message"] == "当前最缺的关键证明是 ds160。"
    assert payload["requested_documents"] == ["ds160"]
    assert payload["gate_progress"] == {
        "overall_status": "waiting_for_parse",
        "ready_count": 0,
        "uploaded_count": 1,
        "missing_count": 1,
        "documents": [
            {
                "document_type": "ds160",
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


def test_funding_alias_upload_stays_usable_in_followup_messages(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 20,
                "narrative_consistency": 55,
                "confidence": 50,
            },
            "requested_documents": [],
        },
    )

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="funding-alias-followup",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "bank_statement"},
        files={
            "file": (
                "bank-statement.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
        },
    )
    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Please continue."},
    )

    assert upload_response.status_code == 202
    assert upload_response.json()["main_flow_feedback"]["status"] == "helpful"

    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["assistant_message"] == "当前最关键的证明是 funding_proof，系统正在等待解析结果。"
    assert payload["requested_documents"] == ["funding_proof"]
    assert payload["gate_progress"] == {
        "overall_status": "waiting_for_parse",
        "ready_count": 0,
        "uploaded_count": 1,
        "missing_count": 0,
        "documents": [
            {
                "document_type": "funding_proof",
                "status": "uploaded",
                "is_uploaded": True,
                "is_parsed": False,
                "meets_minimum_fields": False,
            }
        ],
    }


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
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "Please explain your study plan.",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 40,
                "narrative_consistency": 55,
                "confidence": 50,
            },
            "requested_documents": [],
        },
    )
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
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "Please explain your study plan."
    assert payload["requested_documents"] == []
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
    assert "模拟拒签" in payload["assistant_message"]
    assert "已确认" in payload["assistant_message"]
    assert "hard_conflict" not in payload["assistant_message"]
    assert payload["score_summary"] == {}

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.phase_state == "session_closed"


def test_confirmed_record_conflict_stays_in_high_risk_review(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)
    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    score = ScoreState.model_validate(
        {
            "score_state_id": "score-record-conflict",
            "profile_version": 2,
            "scoring_stage": "interview_turn",
            "category_fit": 48,
            "document_readiness": 55,
            "narrative_consistency": 20,
            "confidence": 81,
            "risk_flags": [
                {
                    "code": "record_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["msg:last_user_turn"],
                }
            ],
            "missing_evidence": [],
        }
    )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.analyze_turn",
        lambda self, record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[
                {
                    "finding_type": "hard_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["msg:hard"],
                }
            ],
        ),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to explain the inconsistency."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "high_risk_review"
    assert payload["governor_decision"] != "simulated_refusal"
    assert payload["score_summary"] == {}


def test_redline_refusal_is_prioritized_when_multiple_confirmed_high_risks_exist(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)
    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    score = ScoreState.model_validate(
        {
            "score_state_id": "score-multi-high-risk",
            "profile_version": 2,
            "scoring_stage": "interview_turn",
            "category_fit": 30,
            "document_readiness": 40,
            "narrative_consistency": 15,
            "confidence": 92,
            "risk_flags": [
                {
                    "code": "record_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["msg:record"],
                },
                {
                    "code": "hard_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["msg:hard"],
                },
            ],
            "missing_evidence": [],
        }
    )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.analyze_turn",
        lambda self, record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[],
            findings=[
                {
                    "finding_type": "hard_conflict",
                    "severity": "high",
                    "status": "confirmed",
                    "evidence_refs": ["msg:hard"],
                }
            ],
        ),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I need a final decision."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "simulated_refusal"
    assert "模拟拒签" in payload["assistant_message"]
    assert payload["score_summary"] == {}


def test_refusal_session_cannot_continue_with_new_messages(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I lied on my DS-160 and used fake bank statements.",
        },
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I still want to keep explaining."},
    )

    assert first.status_code == 200
    assert first.json()["governor_decision"] == "simulated_refusal"
    assert second.status_code == 409
    assert "模拟拒签" in second.json()["detail"]
    assert "不能继续" in second.json()["detail"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert len(turns) == 2


def test_message_turn_persists_current_focus_from_interviewer_runtime(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)
    profile = ApplicantProfile.minimal(f"profile-{session_id}")
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.category_fit = 70
    score.document_readiness = 45
    score.narrative_consistency = 72
    score.confidence = 66
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        )
    ]

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.analyze_turn",
        lambda self, record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[
                RuntimeTraceEntry(
                    node_name="receive_input",
                    summary="user_message_received",
                ),
            ],
            current_focus={"owner": "legacy_runtime", "kind": "decision"},
            interviewer_state={"owner": "legacy_runtime", "next_action": "need_more_evidence"},
        ),
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService._decide_governor",
        lambda self, record, profile, score, trace_entries, findings=None: {
            "decision": "continue_interview",
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        },
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: SimpleNamespace(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.current_focus_json == {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        }
        assert record.interviewer_state_json == {
            "owner": "interviewer_runtime_service",
            "status": "verify_key_issue",
            "public_status": "verify_key_issue",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "next_action": "answer_question",
            "decision_hint": "continue_interview",
            "current_key_question": "What is the purpose of your travel?",
            "current_key_proof": None,
            "current_risk_code": "supporting_evidence_missing",
            "risk_level": "medium",
            "allowed_next_actions": [
                "answer_question",
                "clarify_key_issue",
            ],
            "requested_documents": [],
            "risk_codes": ["supporting_evidence_missing"],
            "history_turn_count": 0,
        }


@pytest.mark.parametrize(
    ("decision", "score", "action", "expected_focus", "expected_state"),
    [
        (
            "need_more_evidence",
            ScoreState.model_validate(
                {
                    "score_state_id": "score-2-interview_turn",
                    "profile_version": 2,
                    "scoring_stage": "interview_turn",
                    "category_fit": 62,
                    "document_readiness": 30,
                    "narrative_consistency": 70,
                    "confidence": 60,
                    "risk_flags": [],
                    "missing_evidence": ["funding_proof"],
                }
            ),
            SimpleNamespace(
                assistant_message="Please upload funding proof.",
                requested_documents=["funding_proof"],
                decision_hint="need_more_evidence",
            ),
            {
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": "funding_proof",
            },
            {
                "owner": "interviewer_runtime_service",
                "status": "waiting_key_proof",
                "public_status": "waiting_key_proof",
                "decision": "need_more_evidence",
                "governor_decision": "need_more_evidence",
                "next_action": "upload_key_proof",
                "decision_hint": "need_more_evidence",
                "current_key_question": None,
                "current_key_proof": "funding_proof",
                "current_risk_code": None,
                "risk_level": "none",
                "allowed_next_actions": [
                    "upload_key_proof",
                    "explain_missing_proof",
                ],
                "requested_documents": ["funding_proof"],
                "risk_codes": [],
                "history_turn_count": 0,
            },
        ),
        (
            "high_risk_review",
            ScoreState.model_validate(
                {
                    "score_state_id": "score-2-interview_turn",
                    "profile_version": 2,
                    "scoring_stage": "interview_turn",
                    "category_fit": 55,
                    "document_readiness": 40,
                    "narrative_consistency": 20,
                    "confidence": 85,
                    "risk_flags": [
                        {
                            "code": "record_conflict",
                            "severity": "high",
                            "status": "confirmed",
                            "evidence_refs": ["msg:last_user_turn"],
                        }
                    ],
                    "missing_evidence": [],
                }
            ),
            SimpleNamespace(
                assistant_message="This case needs additional review.",
                requested_documents=[],
                decision_hint="high_risk_review",
            ),
            {
                "owner": "interviewer_runtime_service",
                "kind": "risk_review",
                "risk_code": "record_conflict",
            },
            {
                "owner": "interviewer_runtime_service",
                "status": "high_risk_review",
                "public_status": "high_risk_review",
                "decision": "high_risk_review",
                "governor_decision": "high_risk_review",
                "next_action": "wait_for_review",
                "decision_hint": "high_risk_review",
                "current_key_question": None,
                "current_key_proof": None,
                "current_risk_code": "record_conflict",
                "risk_level": "high",
                "allowed_next_actions": ["wait_for_review"],
                "requested_documents": [],
                "risk_codes": ["record_conflict"],
                "history_turn_count": 0,
            },
        ),
        (
            "simulated_refusal",
            ScoreState.model_validate(
                {
                    "score_state_id": "score-2-interview_turn",
                    "profile_version": 2,
                    "scoring_stage": "interview_turn",
                    "category_fit": 40,
                    "document_readiness": 35,
                    "narrative_consistency": 10,
                    "confidence": 95,
                    "risk_flags": [
                        {
                            "code": "fraud_admission",
                            "severity": "high",
                            "status": "confirmed",
                            "evidence_refs": ["msg:last_user_turn"],
                        }
                    ],
                    "missing_evidence": [],
                }
            ),
            SimpleNamespace(
                assistant_message="This simulated case results in refusal.",
                requested_documents=[],
                decision_hint="simulated_refusal",
            ),
            {
                "owner": "interviewer_runtime_service",
                "kind": "refusal",
                "risk_code": "fraud_admission",
                "reason": "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，本次会话到此结束。",
            },
            {
                "owner": "interviewer_runtime_service",
                "status": "simulated_refusal",
                "public_status": "simulated_refusal",
                "decision": "simulated_refusal",
                "governor_decision": "simulated_refusal",
                "next_action": "review_refusal_result",
                "decision_hint": "simulated_refusal",
                "current_key_question": None,
                "current_key_proof": None,
                "current_risk_code": "fraud_admission",
                "risk_level": "high",
                "allowed_next_actions": ["review_refusal_result"],
                "requested_documents": [],
                "risk_codes": ["fraud_admission"],
                "history_turn_count": 0,
            },
        ),
    ],
)
def test_message_turn_persists_owner_state_for_non_continue_decisions(
    client: TestClient,
    db_session_factory,
    monkeypatch,
    decision: str,
    score: ScoreState,
    action: SimpleNamespace,
    expected_focus: dict,
    expected_state: dict,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)
    profile = ApplicantProfile.minimal(f"profile-{session_id}")

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.analyze_turn",
        lambda self, record, message_text, recent_turns: SimpleNamespace(
            profile=profile,
            score=score,
            trace_entries=[
                RuntimeTraceEntry(
                    node_name="receive_input",
                    summary="user_message_received",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService._decide_governor",
        lambda self, record, profile, score, trace_entries, current_decision=decision, findings=None: {
            "decision": current_decision,
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": list(score.missing_evidence),
        },
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns, current_action=action: current_action,
    )

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I need a follow-up decision."},
    )

    assert response.status_code == 200
    assert response.json()["governor_decision"] == decision

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.current_focus_json == expected_focus
        assert record.interviewer_state_json == expected_state


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


def test_hard_conflict_signal_closes_session_and_keeps_first_evidence_ref(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I lied about my supporting documents.",
        },
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I want to explain my school plan now.",
        },
    )

    assert first.status_code == 200
    assert second.status_code == 409
    assert first.json()["governor_decision"] == "simulated_refusal"
    assert "模拟拒签" in second.json()["detail"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert record is not None
    first_user_turn = turns[0]
    latest_score_entry = record.score_history_json[-1]
    hard_conflict = next(
        flag for flag in latest_score_entry["risk_flags"] if flag["code"] == "hard_conflict"
    )
    assert record.profile_json["ds160_view"]["last_user_message"] == (
        "I lied about my supporting documents."
    )
    assert hard_conflict["evidence_refs"] == [f"msg:{first_user_turn.turn_id}"]
    assert hard_conflict["evidence_refs"] != ["msg:last_user_turn"]
    assert len(turns) == 2


def test_funding_claim_change_keeps_old_statement_and_allows_continue(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(
        client,
        db_session_factory,
        funding_source="self",
    )

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "Actually not my parents. I will pay for my education."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["governor_decision"] == "continue_interview"
    assert second.json()["governor_decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    claim_history = record.profile_json["ds160_view"]["funding_claim_history"]
    assert [item["value"] for item in claim_history] == ["parents", "self"]
    assert not any(
        flag["code"] == "record_conflict"
        for flag in record.score_history_json[-1]["risk_flags"]
    )


def test_repeated_conflicting_funding_explanations_raise_high_risk(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(
        client,
        db_session_factory,
        funding_source="self",
    )

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My uncle will pay for my studies."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["governor_decision"] == "continue_interview"
    assert second.json()["governor_decision"] == "high_risk_review"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.interviewer_state_json["status"] == "high_risk_review"
    assert record.interviewer_state_json["current_risk_code"] == "record_conflict"
    assert [item["value"] for item in record.profile_json["ds160_view"]["funding_claim_history"]] == [
        "parents",
        "relative",
    ]


def test_evasive_answers_accumulate_into_high_risk(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: SimpleNamespace(
            assistant_message=(
                "This case needs additional review before the interview can continue."
                if governor_decision == "high_risk_review"
                else "Who is funding your education?"
            ),
            requested_documents=[],
            decision_hint=governor_decision,
        ),
    )
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "Who is funding your education?",
        }
        db.add(record)
        db.commit()

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My program is computer science."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My university is in California."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["governor_decision"] == "continue_interview"
    assert second.json()["governor_decision"] == "high_risk_review"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.profile_json["ds160_view"]["risk_watch"]["evasive_turn_count"] == 2
    assert any(
        flag["code"] == "evasive_answer"
        for flag in record.score_history_json[-1]["risk_flags"]
    )


def test_repeated_conflicting_funding_explanations_still_escalate_after_turn_window_rolls(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(
        client,
        db_session_factory,
        funding_source="self",
    )

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    assert first.status_code == 200
    assert first.json()["governor_decision"] == "continue_interview"

    for filler in [
        "I will study computer science.",
        "My program lasts two years.",
        "The school is in California.",
        "I plan to return after graduation.",
        "I chose this university for research fit.",
    ]:
        response = client.post(
            f"/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": filler},
        )
        assert response.status_code == 200

    final_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My uncle will pay for my studies."},
    )

    assert final_response.status_code == 200
    assert final_response.json()["governor_decision"] == "high_risk_review"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    archived_history = record.profile_json["ds160_view"]["field_claim_history"][
        "/funding/primary_source"
    ]
    assert archived_history[0]["value"] == "parents"
    assert archived_history[-1]["value"] == "relative"


def test_missing_key_proof_across_turns_escalates_to_high_risk(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(
        client,
        db_session_factory,
        funding_source="parents",
        documented_funding=False,
    )

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        }
        db.add(record)
        db.commit()

    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will upload the funding proof later."},
    )
    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I still do not have the bank statement yet."},
    )

    assert first.status_code == 200
    assert second.status_code == 200
    assert first.json()["governor_decision"] == "need_more_evidence"
    assert second.json()["governor_decision"] == "high_risk_review"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.profile_json["ds160_view"]["risk_watch"]["missing_key_proof_turn_count"] == 2
    assert any(
        flag["code"] == "unresolved_key_proof_gap"
        for flag in record.score_history_json[-1]["risk_flags"]
    )
