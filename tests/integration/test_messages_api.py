from collections.abc import Generator
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine, func, select
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
from app.agents.user_model_config import current_user_model_config
from app.core import settings as settings_module
from app.services.runtime_errors import ModelRuntimeError
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


def assert_interviewer_state_matches_new_contract(
    actual_state: dict,
    expected_state: dict,
) -> None:
    assert actual_state | {
        "advisory_context": actual_state["advisory_context"],
        "prompt_trace": actual_state["prompt_trace"],
        "remaining_required_documents": actual_state.get(
            "remaining_required_documents",
            [],
        ),
        "document_review": actual_state.get("document_review", {}),
    } == expected_state | {
        "advisory_context": actual_state["advisory_context"],
        "prompt_trace": actual_state["prompt_trace"],
        "remaining_required_documents": expected_state.get(
            "remaining_required_documents",
            [],
        ),
        "document_review": expected_state.get("document_review", {}),
    }


def parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for raw_event in body.split("\n\n"):
        if not raw_event.strip():
            continue
        event_name = None
        event_data = None
        for line in raw_event.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            if line.startswith("data:"):
                event_data = json.loads(line.removeprefix("data:").strip())
        if event_name is not None and isinstance(event_data, dict):
            events.append((event_name, event_data))
    return events


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
                        "document_type": filename.removesuffix(".txt"),
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


def install_stub_build_question_action(
    monkeypatch: pytest.MonkeyPatch,
    *,
    continue_interview_message: str = "What is the purpose of your travel?",
    high_risk_message: str = (
        "This case needs additional review before the interview can continue."
    ),
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
        del self, session_id, profile, trace_entries, recent_turns
        requested_documents = list(score.missing_evidence[:1])
        if governor_decision == "need_more_evidence":
            return SimpleNamespace(
                assistant_message=(
                    f"Please upload {requested_documents[0]}."
                    if requested_documents
                    else "Please provide the key supporting document for this point."
                ),
                requested_documents=requested_documents,
                decision_hint="need_more_evidence",
            )
        if governor_decision == "high_risk_review":
            return SimpleNamespace(
                assistant_message=high_risk_message,
                requested_documents=[],
                decision_hint="high_risk_review",
            )
        if governor_decision == "simulated_refusal":
            return SimpleNamespace(
                assistant_message="This simulated case results in refusal.",
                requested_documents=[],
                decision_hint="simulated_refusal",
            )
        if governor_decision == "route_correction":
            return SimpleNamespace(
                assistant_message="Your case may fit a different visa route.",
                requested_documents=[],
                decision_hint="route_correction",
            )
        if requested_documents:
            return SimpleNamespace(
                assistant_message=f"Please upload {requested_documents[0]}.",
                requested_documents=requested_documents,
                decision_hint="need_more_evidence",
            )
        return SimpleNamespace(
            assistant_message=continue_interview_message,
            requested_documents=[],
            decision_hint="continue_interview",
        )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        fake_build_question_action,
    )


def install_fixed_build_question_action(
    monkeypatch: pytest.MonkeyPatch,
    *,
    decision: str,
    assistant_message: str,
    requested_documents: list[str] | None = None,
):
    def fake_build_question_action(
        self,
        session_id,
        profile,
        score,
        governor_decision,
        trace_entries,
        recent_turns=None,
    ):
        del self, session_id, profile, score, governor_decision, trace_entries, recent_turns
        return SimpleNamespace(
            assistant_message=assistant_message,
            requested_documents=list(requested_documents or []),
            decision_hint=decision,
        )

    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        fake_build_question_action,
    )


def test_message_turn_enters_runtime_when_gate_not_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "What is your study plan?",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
        },
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is your study plan?"
    assert payload["requested_documents"] == []
    assert payload["score_summary"] == {}
    assert payload["gate_progress"] == {
        "overall_status": "pending_documents",
        "ready_count": 0,
        "uploaded_count": 0,
        "missing_count": 3,
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
        ],
    }


def test_message_post_is_idempotent_for_repeated_client_message_id(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled once: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    body = {
        "role": "user",
        "content": "My parents will pay for my studies.",
        "client_message_id": "client-repeat-1",
    }
    first_response = client.post(f"/v1/sessions/{session_id}/messages", json=body)
    second_response = client.post(f"/v1/sessions/{session_id}/messages", json=body)

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["assistant_message"] == first_response.json()[
        "assistant_message"
    ]
    assert second_response.json()["idempotent_replay"] is True

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].metadata_json["client_message_id"] == "client-repeat-1"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "interview"
        assert record.gate_status_json["status"] == "pending_documents"


def test_message_user_turn_commits_before_runtime_call(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_seen_user_turns: list[int] = []

    def fake_run_turn(self, record, message_text):
        with db_session_factory() as db:
            run_seen_user_turns.append(
                db.scalar(
                    select(func.count())
                    .select_from(SessionTurnRecord)
                    .where(
                        SessionTurnRecord.session_id == record.session_id,
                        SessionTurnRecord.role == "user",
                        SessionTurnRecord.client_message_id == "client-commit-1",
                    )
                )
            )
        return {
            "assistant_message": f"handled after commit: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My parents will pay for my studies.",
            "client_message_id": "client-commit-1",
        },
    )

    assert response.status_code == 200
    assert run_seen_user_turns == [1]


def test_message_turn_records_interview_memory_for_answered_question(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "毕业后你准备做什么工作？",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {},
            "turn_record": {
                "turn_id": "placeholder",
                "session_id": record.session_id,
                "user_turn_id": "placeholder",
                "user_input": message_text,
                "decision": "continue_interview",
                "assistant_message": "毕业后你准备做什么工作？",
                "requested_documents": [],
                "remaining_required_documents": [],
                "focus": {
                    "kind": "interview_question",
                    "question": "毕业后你准备做什么工作？",
                },
            },
        },
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]
    first = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "我去美国读数据科学。"},
    )
    assert first.status_code == 200

    second = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "我毕业后会回国做数据分析师。"},
    )

    assert second.status_code == 200
    with db_session_factory() as db:
        user_turns = db.scalars(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "user",
            )
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert user_turns[-1].metadata_json["interview_memory"] == {
        "schema_version": "interview_memory.v1",
        "kind": "oral_answer",
        "topic": "post_study_plan",
        "topic_label": "毕业后计划",
        "status": "answered",
        "closed": True,
        "question_turn_id": user_turns[-1].metadata_json["interview_memory"][
            "question_turn_id"
        ],
        "question_turn_index": 2,
        "question": "毕业后你准备做什么工作？",
        "answer_turn_id": user_turns[-1].turn_id,
        "answer_turn_index": user_turns[-1].turn_index,
        "answer_excerpt": "我毕业后会回国做数据分析师。",
        "confidence": 0.74,
    }


def test_message_turn_graph_starts_without_materials_after_f1_selection(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("graph runtime should own no-material F-1 chat")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_runtime"] == "graph"
    assert payload["assistant_message"] == "为什么选择去美国读这个项目？"
    assert payload["requested_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "pending_documents"

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        document_count = db.scalar(
            select(func.count())
            .select_from(DocumentRecord)
            .where(DocumentRecord.session_id == session_id)
        )

    assert document_count == 0
    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "graph_runtime_adapter"),
    ]


def test_messages_reject_user_model_config_when_disabled(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My parents will pay.",
            "model_config": {
                "base_url": "https://models.example.test/v1",
                "api_key": "user-key",
                "model": "user-model",
            },
        },
    )

    assert response.status_code == 403
    assert "未启用用户自定义模型配置" in response.json()["detail"]


def test_messages_apply_user_model_config_for_request_scope(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_configs = []
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", True)

    def fake_run_turn(self, record, message_text: str) -> dict:
        captured_configs.append(current_user_model_config())
        return {
            "assistant_message": "Which school will you attend?",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My parents will pay.",
            "model_config": {
                "base_url": "https://models.example.test",
                "api_key": "user-key",
                "model": "user-model",
            },
        },
    )

    assert response.status_code == 200
    assert response.json()["assistant_message"] == "Which school will you attend?"
    assert len(captured_configs) == 1
    runtime_config = captured_configs[0]
    assert runtime_config is not None
    assert runtime_config.base_url == "https://models.example.test/v1"
    assert runtime_config.api_key == "user-key"
    assert runtime_config.model == "user-model"


def test_messages_stream_allows_default_model_without_user_streaming_switch(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", False)

    def fake_run_turn(self, record, message_text: str) -> dict:
        del self
        turn_record = {
            "turn_id": "turn-stream-default-model",
            "session_id": record.session_id,
            "user_input": message_text,
            "decision": "continue_interview",
            "assistant_message": "What will you study?",
            "requested_documents": [],
            "remaining_required_documents": [],
            "focus": {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": "What will you study?",
            },
            "trace_refs": ["turn_decision"],
            "advisory_summary": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
            "document_review": {},
        }
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.current_focus_json = dict(turn_record["focus"])
        record.interviewer_state_json = {
            "decision": "continue_interview",
            "current_focus": dict(turn_record["focus"]),
            "document_review": {},
        }
        return {
            "assistant_message": turn_record["assistant_message"],
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "runtime_view_state": {
                "decision": "continue_interview",
                "current_focus": dict(turn_record["focus"]),
            },
            "prompt_trace": {
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
            },
            "turn_record": turn_record,
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/messages/stream",
        json={
            "role": "user",
            "content": "I will study computer science.",
        },
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: analyzing" in body
    assert "event: final" in body
    events = parse_sse_events(body)
    assert events[-1][1]["assistant_message"] == "What will you study?"


def test_messages_stream_requires_switch_for_user_model_config(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", True)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", False)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages/stream",
        json={
            "role": "user",
            "content": "My parents will pay.",
            "model_config": {
                "base_url": "https://models.example.test",
                "api_key": "user-key",
                "model": "user-model",
            },
        },
    )

    assert response.status_code == 403
    assert "未启用用户模型流式输出" in response.json()["detail"]


def test_messages_stream_emits_final_payload_contract(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", True)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", True)

    def fake_run_turn(self, record, message_text: str) -> dict:
        del self
        turn_record = {
            "turn_id": "turn-stream-stub",
            "session_id": record.session_id,
            "user_input": message_text,
            "decision": "continue_interview",
            "assistant_message": "Who will pay your first year expenses?",
            "requested_documents": [],
            "remaining_required_documents": [],
            "focus": {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": "Who will pay your first year expenses?",
            },
            "trace_refs": ["turn_decision"],
            "advisory_summary": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
            "document_review": {},
        }
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.current_focus_json = dict(turn_record["focus"])
        record.interviewer_state_json = {
            "decision": "continue_interview",
            "current_focus": dict(turn_record["focus"]),
            "document_review": {},
        }
        return {
            "assistant_message": turn_record["assistant_message"],
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "runtime_view_state": {
                "decision": "continue_interview",
                "current_focus": {
                    "kind": "interview_question",
                    "question": "Who will pay your first year expenses?",
                },
            },
            "prompt_trace": {
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
            },
            "turn_record": turn_record,
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/messages/stream",
        json={
            "role": "user",
            "content": "My father will pay.",
            "model_config": {
                "base_url": "https://models.example.test",
                "api_key": "user-key",
                "model": "user-model",
            },
        },
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: analyzing" in body
    assert "event: final" in body
    events = parse_sse_events(body)
    assert [event for event, _data in events] == ["accepted", "analyzing", "final"]
    final_payload = events[-1][1]
    assert final_payload["assistant_message"] == "Who will pay your first year expenses?"
    assert final_payload["turn_decision"]["decision"] == "continue_interview"
    assert final_payload["runtime_view_state"]
    assert final_payload["prompt_trace"]
    assert final_payload["requested_documents"] == []


def test_messages_stream_graph_shadow_keeps_final_payload_public(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")

    def fake_run_turn(self, record, message_text: str) -> dict:
        del self
        turn_record = {
            "turn_id": "turn-stream-graph-shadow",
            "session_id": record.session_id,
            "user_input": message_text,
            "decision": "continue_interview",
            "assistant_message": "What will you study?",
            "requested_documents": [],
            "remaining_required_documents": [],
            "focus": {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": "What will you study?",
            },
            "trace_refs": ["turn_decision"],
            "advisory_summary": {
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
            },
            "document_review": {},
        }
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.current_focus_json = dict(turn_record["focus"])
        record.interviewer_state_json = {
            "decision": "continue_interview",
            "current_focus": dict(turn_record["focus"]),
            "document_review": {},
        }
        return {
            "assistant_message": turn_record["assistant_message"],
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
            },
            "turn_record": turn_record,
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/messages/stream",
        json={"role": "user", "content": "I will study computer science."},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    events = parse_sse_events(body)
    assert [event for event, _data in events] == ["accepted", "analyzing", "final"]
    final_payload = events[-1][1]
    assert final_payload["assistant_message"] == "What will you study?"
    assert "graph_shadow" not in final_payload

    with db_session_factory() as db:
        assistant_turn = db.scalar(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        )

    assert assistant_turn is not None
    assert assistant_turn.metadata_json["graph_shadow"]["status"] == "completed"


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


def test_message_turn_uses_adjudication_agent_output_for_continue_interview(
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
        if module_key == "adjudication_agent"
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
    assert payload["runtime_view_state"]["decision"] == "continue_interview"
    assert payload["runtime_view_state"]["governor_decision"] == "continue_interview"
    assert payload["runtime_view_state"]["current_key_question"] == (
        "What is the purpose of your travel?"
    )
    assert payload["runtime_view_state"]["prompt_trace"] == payload["prompt_trace"]
    assert payload["turn_decision"]["current_key_question"] == (
        "What is the purpose of your travel?"
    )

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
            "decide_capability",
            "resolve_capability",
            "turn_decision",
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


def test_message_turn_graph_shadow_keeps_legacy_response_and_single_assistant_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
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
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert "graph_shadow" not in payload

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "interviewer_runtime_service"),
    ]
    assert record is not None
    assert [entry["node_name"] for entry in record.runtime_trace_json] == [
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
    graph_shadow = turns[1].metadata_json["graph_shadow"]
    assert graph_shadow["status"] == "completed"
    assert graph_shadow["agent_runtime"] == "graph_shadow"
    assert graph_shadow["graph_run_id"].startswith("graph-run-")
    assert graph_shadow["graph_trace"]["event_count"] > 0
    assert graph_shadow["turn_decision"]["decision"] == "continue_interview"


def test_message_turn_graph_shadow_failure_fails_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", True)
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.GraphRuntimeAdapter.run_turn",
        lambda self, record, message_text, user_turn=None: (_ for _ in ()).throw(
            RuntimeError("shadow exploded")
        ),
    )
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
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    assert response.json()["assistant_message"] == "What is the purpose of your travel?"

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "interviewer_runtime_service"),
    ]
    graph_shadow = turns[1].metadata_json["graph_shadow"]
    assert graph_shadow == {
        "status": "error",
        "agent_runtime": "graph_shadow",
        "error_type": "RuntimeError",
        "error_message": "shadow exploded",
    }


def test_message_turn_graph_mode_writes_public_response_and_single_assistant_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")

    def legacy_must_not_run(self, record, message_text: str) -> dict:
        raise AssertionError("legacy runtime should not run in graph mode")

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        legacy_must_not_run,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "为什么选择去美国读这个项目？"
    assert payload["agent_runtime"] == "graph"
    assert payload["graph_run_id"].startswith("graph-run-")
    assert payload["graph_trace"]["event_count"] > 0
    assert "graph_events" not in payload
    assert "graph_runtime_error" not in payload
    assert payload["turn_decision"]["assistant_message_author"] == "adjudication_agent"
    assert payload["prompt_trace"]["graph_run_id"] == payload["graph_run_id"]
    assert payload["runtime_view_state"]["source_turn_id"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "graph_runtime_adapter"),
    ]
    assistant_turn = turns[1]
    metadata = assistant_turn.metadata_json
    assert metadata["agent_runtime"] == "graph"
    assert metadata["graph_run_id"] == payload["graph_run_id"]
    assert metadata["runtime_view_state"]["source_turn_id"] == assistant_turn.turn_id
    assert metadata["runtime_view_state"]["prompt_trace"]["graph_run_id"] == payload["graph_run_id"]
    assert metadata["turn_record"]["assistant_turn_id"] == assistant_turn.turn_id
    assert metadata["turn_record"]["user_turn_id"] == turns[0].turn_id
    assert metadata["graph_events"][-1]["event_type"] == "final"
    assert record is not None
    assert record.current_governor_decision == "continue_interview"
    assert record.interviewer_state_json["owner"] == "graph_runtime"


def test_message_turn_graph_canary_hundred_percent_uses_graph(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_canary")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 100)
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run when canary selects graph")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    assert response.json()["agent_runtime"] == "graph"

    with db_session_factory() as db:
        assistant_turn = db.scalar(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        )

    assert assistant_turn is not None
    assert assistant_turn.source == "graph_runtime_adapter"


def test_message_turn_graph_failure_fails_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", True)
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.GraphRuntimeAdapter.run_turn",
        lambda self, record, message_text, user_turn=None: (_ for _ in ()).throw(
            RuntimeError("graph exploded")
        ),
    )
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
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert "graph_runtime_error" not in payload

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "interviewer_runtime_service"),
    ]
    assert turns[1].metadata_json["graph_runtime_error"] == {
        "status": "error",
        "agent_runtime": "graph",
        "selected_public_runtime": "graph",
        "error_type": "RuntimeError",
        "error_message": "graph exploded",
        "fallback_runtime": "legacy",
    }


def test_message_turn_graph_typed_adjudication_missing_model_uses_safe_fallback(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(
        settings_module.settings,
        "agent_runtime_typed_adjudication_enabled",
        True,
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run for graph typed fallback")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_runtime"] == "graph"
    assert payload["turn_decision"]["assistant_message_author"] == (
        "deterministic_safe_fallback"
    )
    assert payload["turn_decision"]["guard_status"] == "fallback_required"

    with db_session_factory() as db:
        assistant_turn = db.scalar(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        )

    assert assistant_turn is not None
    adjudication_event = next(
        event
        for event in assistant_turn.metadata_json["graph_events"]
        if event["event_type"] == "adjudication_completed"
    )
    assert adjudication_event["payload"]["fallback_used"] is True
    assert adjudication_event["payload"]["fallback_reason"] == "model_unavailable"
    assert adjudication_event["payload"]["llm_calls_used"] == 0


def test_messages_stream_graph_mode_keeps_sse_contract(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/messages/stream",
        json={"role": "user", "content": "I will study computer science."},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    events = parse_sse_events(body)
    assert [event for event, _data in events] == ["accepted", "analyzing", "final"]
    final_payload = events[-1][1]
    assert final_payload["agent_runtime"] == "graph"
    assert final_payload["assistant_message"] == "为什么选择去美国读这个项目？"
    assert "graph_events" not in final_payload


def test_message_turn_returns_503_when_adjudication_agent_config_missing_for_interview_question(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I want to study in the U.S."},
    )
    assert response.status_code == 503
    assert "OPENAI_API_KEY" in response.json()["detail"]
    assert "OPENAI_BASE_URL" in response.json()["detail"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert turns == []
    assert record is not None
    assert not record.runtime_trace_json


def test_message_turn_persists_turn_record_on_assistant_turn_metadata(
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
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    user_turn = turns[0]
    assistant_turn = turns[1]
    turn_record = assistant_turn.metadata_json["turn_record"]
    runtime_view_state = assistant_turn.metadata_json["runtime_view_state"]

    assert turn_record["turn_id"] == assistant_turn.turn_id
    assert turn_record["assistant_turn_id"] == assistant_turn.turn_id
    assert turn_record["user_turn_id"] == user_turn.turn_id
    assert turn_record["session_id"] == session_id
    assert turn_record["decision"] == "continue_interview"
    assert turn_record["assistant_message"] == "What is the purpose of your travel?"
    assert turn_record["trace_refs"] == [
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
    assert assistant_turn.metadata_json["requested_documents"] == []
    assert assistant_turn.metadata_json["turn_decision"] == "continue_interview"
    assert runtime_view_state["source_turn_id"] == assistant_turn.turn_id
    assert runtime_view_state["decision"] == "continue_interview"
    assert runtime_view_state["current_key_question"] == (
        "What is the purpose of your travel?"
    )


def test_message_turn_returns_429_when_adjudication_agent_quota_is_exhausted(
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
                    "assistant_message": "This value should never be used.",
                    "requested_documents": [],
                    "decision_hint": "continue_interview",
                },
            ),
            {"model": "gpt-5.4"},
        )
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AdjudicationAgentRunner.run",
        lambda self, **kwargs: (_ for _ in ()).throw(
            ModelRuntimeError(
                detail="当前对话模型额度已耗尽，请稍后重试或更换可用配置。",
                status_code=429,
                provider="openai_compatible",
                model="gpt-5.4",
                upstream_code="API_KEY_QUOTA_EXHAUSTED",
                body={
                    "code": "API_KEY_QUOTA_EXHAUSTED",
                    "message": "API key 额度已用完",
                },
            )
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 429
    assert "额度已耗尽" in response.json()["detail"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert turns == []
    assert record is not None
    assert not record.runtime_trace_json


def test_message_turn_returns_503_when_adjudication_agent_output_is_invalid(
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
        if module_key == "adjudication_agent"
        else (None, {"model": None}),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 503
    assert "模型运行失败" in response.json()["detail"]


def test_message_turn_rejects_non_user_role(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 422


def test_supporting_funding_upload_does_not_block_interview_flow(
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
        "overall_status": "pending_documents",
        "ready_count": 0,
        "uploaded_count": 0,
        "missing_count": 3,
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
        ],
    }
    assert upload_response.status_code == 202


def test_fast_upload_does_not_block_next_message_while_parse_is_pending(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "Please explain your travel purpose.",
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

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("upload response must not call extraction model")
        ),
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
    assert upload_response.json()["main_flow_feedback"]["status"] == "helpful"

    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["assistant_message"] == "Please explain your travel purpose."
    assert payload["requested_documents"] == []
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


def test_helpful_secondary_upload_enters_gate_flow_without_hijacking_primary_focus(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "Please explain your travel purpose.",
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
    assert upload_response.json()["main_flow_feedback"]["status"] == "helpful"

    assert message_response.status_code == 200
    payload = message_response.json()
    assert payload["assistant_message"] == "Please explain your travel purpose."
    assert payload["requested_documents"] == []
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
            "assistant_message": "Please explain your funding plan.",
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
    assert payload["assistant_message"] == "Please explain your funding plan."
    assert payload["requested_documents"] == []
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
                    "document_type": "ds160",
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
                    "document_type": "passport_bio",
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
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="simulated_refusal",
        assistant_message="当前模拟结果为拒签。已确认存在严重材料真实性冲突，本次面签不再继续。",
    )
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
    assert "拒签" in payload["assistant_message"]
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
    install_fixed_build_question_action(
        monkeypatch,
        decision="high_risk_review",
        assistant_message="当前案例需要先进入高风险复核，正式问答暂时不能继续。",
    )
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
            findings=[],
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
    install_fixed_build_question_action(
        monkeypatch,
        decision="simulated_refusal",
        assistant_message="当前模拟结果为拒签。已确认存在严重红线冲突，本次面签结束。",
    )
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
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="simulated_refusal",
        assistant_message="当前模拟结果为拒签。已确认存在严重材料真实性冲突，本次面签不再继续。",
    )
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
    assert "拒签" in second.json()["detail"]
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
        assert_interviewer_state_matches_new_contract(
            record.interviewer_state_json,
            {
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
            },
        )


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
                "kind": "interview_question",
                "question": "这份材料我看到了。你这次赴美学习什么项目？",
            },
            {
                "owner": "interviewer_runtime_service",
                "status": "continue_interview",
                "public_status": "continue_interview",
                "decision": "continue_interview",
                "governor_decision": "continue_interview",
                "next_action": "answer_question",
                "decision_hint": "continue_interview",
                "current_key_question": "这份材料我看到了。你这次赴美学习什么项目？",
                "current_key_proof": None,
                "current_risk_code": None,
                "risk_level": "none",
                "allowed_next_actions": [
                    "answer_question",
                    "continue_interview",
                ],
                "requested_documents": [],
                "remaining_required_documents": [],
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
            "decision": (
                "continue_interview"
                if current_decision == "need_more_evidence"
                else current_decision
            ),
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
    expected_response_decision = (
        "continue_interview" if decision == "need_more_evidence" else decision
    )
    assert response.json()["governor_decision"] == expected_response_decision

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.current_focus_json == expected_focus
        assert_interviewer_state_matches_new_contract(
            record.interviewer_state_json,
            expected_state,
        )


def test_negated_fraud_statement_does_not_trigger_refusal(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="continue_interview",
        assistant_message="What is the purpose of your travel?",
    )
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
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="simulated_refusal",
        assistant_message="当前模拟结果为拒签。已确认存在严重材料真实性冲突，本次面签不再继续。",
    )
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
    assert "拒签" in second.json()["detail"]

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
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="continue_interview",
        assistant_message="What is the purpose of your travel?",
    )
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


def test_repeated_conflicting_funding_explanations_no_longer_auto_raise_high_risk(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="continue_interview",
        assistant_message="What is the purpose of your travel?",
    )
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
    assert second.json()["governor_decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.interviewer_state_json["status"] != "high_risk_review"
    assert [item["value"] for item in record.profile_json["ds160_view"]["funding_claim_history"]] == [
        "parents",
        "relative",
    ]


def test_evasive_answers_no_longer_accumulate_into_hard_risk_watch(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="continue_interview",
        assistant_message="Who is funding your education?",
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
    assert second.json()["governor_decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert "risk_watch" not in record.profile_json["ds160_view"]


def test_repeated_conflicting_funding_explanations_no_longer_auto_escalate_after_turn_window_rolls(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="continue_interview",
        assistant_message="What is the purpose of your travel?",
    )
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
    assert final_response.json()["governor_decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    archived_history = record.profile_json["ds160_view"]["field_claim_history"][
        "/funding/primary_source"
    ]
    assert archived_history[0]["value"] == "parents"
    assert archived_history[-1]["value"] == "relative"


def test_missing_key_proof_across_turns_stays_need_more_evidence_without_hard_watch(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_fixed_build_question_action(
        monkeypatch,
        decision="need_more_evidence",
        assistant_message="Please provide the key supporting document for this point.",
    )
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
    assert second.json()["governor_decision"] == "need_more_evidence"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert "risk_watch" not in record.profile_json["ds160_view"]
