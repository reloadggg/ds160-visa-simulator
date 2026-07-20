from collections.abc import Generator
import inspect
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import (
    AdminSettingRecord,
    DocumentRecord,
    SessionRecord,
    SessionTurnRecord,
)
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
from app.platform.runtime_ledger import RuntimeViewState
from app.services.message_service import MessageService
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput
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

def assert_native_canonical_runtime_execution(
    payload: dict,
    *,
    configured_runtime: str = "native_interviewer",
    compatibility_runtime_label: str | None = None,
    source: str = "message_turn",
) -> None:
    runtime_execution = payload["runtime_execution"]
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert runtime_execution["configured_runtime"] == configured_runtime
    assert runtime_execution["requested_public_runtime"] == "native_interviewer"
    assert runtime_execution["public_runtime"] == "native_interviewer"
    assert runtime_execution["runtime_role"] == "canonical"
    assert runtime_execution["canonical"] is True
    assert runtime_execution["execution_runtime"] == "native_interviewer_runtime"
    assert runtime_execution["source"] == source
    if compatibility_runtime_label is None:
        assert "compatibility_runtime_label" not in runtime_execution
    else:
        assert runtime_execution["compatibility_runtime_label"] == compatibility_runtime_label

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


def enable_admin_user_model_config(db_session_factory) -> None:
    with db_session_factory() as db:
        db.merge(
            AdminSettingRecord(
                setting_key="demo",
                value_json={"user_model_config_enabled": True},
            )
        )
        db.commit()


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


def install_native_run_turn_stub(
    monkeypatch: pytest.MonkeyPatch,
    run_turn,
) -> None:
    signature = inspect.signature(run_turn)
    accepts_user_turn = "user_turn" in signature.parameters or any(
        parameter.kind == inspect.Parameter.VAR_KEYWORD
        for parameter in signature.parameters.values()
    )

    def native_run_turn(self, record, message_text, user_turn=None):
        if accepts_user_turn:
            return run_turn(self, record, message_text, user_turn=user_turn)
        return run_turn(self, record, message_text)

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        native_run_turn,
    )


def install_native_interviewer_stub(
    monkeypatch: pytest.MonkeyPatch,
    *,
    assistant_message: str = "你提到想学计算机方向。这个项目和你毕业后的计划具体怎么衔接？",
    decision: str = "continue_interview",
    requested_documents: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService._build_runtime",
        lambda self, declared_family: {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        },
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAIAgentsInterviewerRunner.run",
        lambda self, **kwargs: NativeInterviewerOutput(
            assistant_message=assistant_message,
            decision=decision,
            requested_documents=list(requested_documents or []),
        ),
    )


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


def install_native_fixed_interview_response(
    monkeypatch: pytest.MonkeyPatch,
    *,
    decision: str,
    assistant_message: str,
    requested_documents: list[str] | None = None,
):
    active_requested_documents = list(
        requested_documents or (["funding_proof"] if decision == "need_more_evidence" else [])
    )

    def fake_run_turn(self, record, message_text, user_turn=None):
        del self
        if decision == "need_more_evidence":
            current_focus = {
                "owner": "native_interviewer",
                "kind": "required_document",
                "document_type": active_requested_documents[0] if active_requested_documents else None,
            }
            current_key_question = None
            current_key_proof = current_focus.get("document_type")
            allowed_next_actions = ["upload_key_proof", "explain_missing_proof"]
        elif decision == "high_risk_review":
            current_focus = {
                "owner": "native_interviewer",
                "kind": "risk_review",
                "question": assistant_message,
            }
            current_key_question = assistant_message
            current_key_proof = None
            allowed_next_actions = ["clarify_key_issue", "wait_for_review"]
        elif decision == "simulated_refusal":
            current_focus = {
                "owner": "native_interviewer",
                "kind": "refusal",
                "reason": assistant_message,
            }
            current_key_question = None
            current_key_proof = None
            allowed_next_actions = ["review_refusal_result"]
        else:
            current_focus = {
                "owner": "native_interviewer",
                "kind": "interview_question",
                "question": assistant_message,
            }
            current_key_question = assistant_message
            current_key_proof = None
            allowed_next_actions = ["answer_question", "continue_interview"]

        advisory_context = {
            "score_summary": {},
            "risk_codes": [],
            "missing_evidence": active_requested_documents if decision == "need_more_evidence" else [],
            "risk_level": "none",
        }
        prompt_trace = {
            "prompt_pack_id": "ds160.native_interviewer",
            "prompt_version": "native-v0",
            "native_run_id": "native-run-fixed-stub",
            "assistant_message_author": "native_interviewer",
        }
        runtime_view_state = {
            "source_turn_id": None,
            "decision": decision,
            "governor_decision": decision,
            "public_status": decision,
            "risk_level": advisory_context["risk_level"],
            "current_focus": current_focus,
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": None,
            "requested_documents": active_requested_documents,
            "remaining_required_documents": active_requested_documents if decision == "need_more_evidence" else [],
            "allowed_next_actions": allowed_next_actions,
            "advisory_context": advisory_context,
            "document_review": {},
            "prompt_trace": prompt_trace,
        }
        return {
            "assistant_message": assistant_message,
            "governor_decision": decision,
            "score_summary": {},
            "requested_documents": active_requested_documents,
            "remaining_required_documents": active_requested_documents if decision == "need_more_evidence" else [],
            "gate_progress": {},
            "turn_decision": {
                "decision": decision,
                "assistant_message_author": "native_interviewer",
                "requested_documents": active_requested_documents,
                "remaining_required_documents": active_requested_documents if decision == "need_more_evidence" else [],
                "governor_decision": decision,
                "next_safe_action": "continue_interview",
                "current_key_question": current_key_question,
                "current_key_proof": current_key_proof,
                "current_risk_code": None,
            },
            "document_review": {},
            "advisory_context": advisory_context,
            "prompt_trace": prompt_trace,
            "runtime_view_state": runtime_view_state,
            "turn_record": {
                "turn_id": getattr(user_turn, "turn_id", None) or f"{record.session_id}:pending-turn",
                "session_id": record.session_id,
                "user_turn_id": getattr(user_turn, "turn_id", None),
                "user_input": message_text,
                "decision": decision,
                "assistant_message": assistant_message,
                "requested_documents": active_requested_documents,
                "remaining_required_documents": active_requested_documents if decision == "need_more_evidence" else [],
                "focus": current_focus,
                "trace_refs": ["native_interviewer"],
                "artifacts": [],
                "advisory_summary": {},
                "document_review": {},
            },
            "agent_runtime": "native_interviewer",
            "selected_public_runtime": "native_interviewer",
            "native_run_id": "native-run-fixed-stub",
        }

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

def test_message_turn_enters_runtime_when_gate_not_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_native_run_turn_stub(
        monkeypatch,
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

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
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

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
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




def test_message_post_retries_transient_provider_failures_then_succeeds(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        if run_count < 3:
            raise ModelRuntimeError(
                detail="temporary provider connection failure",
                status_code=502,
                provider="openai_compatible",
                model="gpt-5.4",
                upstream_code="upstream_connection_error",
                error_category="upstream_connection_error",
            )
        return {
            "assistant_message": f"recovered after retries: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My parents will pay for my first year.",
            "client_message_id": "client-provider-retry-success-1",
        },
    )

    assert response.status_code == 200
    assert run_count == 3
    assert response.json()["assistant_message"].startswith("recovered after retries")

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]
    assert turns[0].client_message_id == "client-provider-retry-success-1"



def test_message_post_retries_transient_rate_limit_429(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        if run_count == 1:
            raise ModelRuntimeError(
                detail="模型服务临时限流，请稍后重试。",
                status_code=429,
                provider="openai_compatible",
                model="gpt-5.4",
                upstream_code="rate_limit_exceeded",
                body={"code": "rate_limit_exceeded"},
            )
        return {
            "assistant_message": "rate limit recovered",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will sponsor me."},
    )

    assert response.status_code == 200
    assert run_count == 2
    assert response.json()["assistant_message"] == "rate limit recovered"

def test_message_post_exhausted_transient_provider_failure_cleans_user_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        raise ModelRuntimeError(
            detail="temporary provider unavailable",
            status_code=503,
            provider="openai_compatible",
            model="gpt-5.4",
            upstream_code="service_unavailable",
            error_category="upstream_model",
        )

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My father owns a company.",
            "client_message_id": "client-provider-retry-exhausted-1",
        },
    )

    assert response.status_code == 503
    assert run_count == 3
    detail = response.json()["detail"]
    assert detail["retry_attempts"] == 2
    assert detail["retry_exhausted"] is True

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert turns == []


def test_message_post_does_not_retry_quota_or_auth_provider_failures(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        raise ModelRuntimeError(
            detail="当前对话模型额度已耗尽，请更换可用配置。",
            status_code=429,
            provider="openai_compatible",
            model="gpt-5.4",
            upstream_code="API_KEY_QUOTA_EXHAUSTED",
            body={"code": "API_KEY_QUOTA_EXHAUSTED", "message": "余额不足"},
        )

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My mother will sponsor me."},
    )

    assert response.status_code == 429
    assert run_count == 1
    detail = response.json()["detail"]
    assert detail["upstream_code"] == "API_KEY_QUOTA_EXHAUSTED"
    assert "retry_attempts" not in detail

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session_id)
        ).all()
    assert turns == []


def test_message_post_does_not_retry_model_config_provider_failure(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        raise ModelRuntimeError(
            detail="当前对话模型配置不可用，请检查模型设置。",
            status_code=503,
            provider="openai_compatible",
            model="gpt-5.4",
            upstream_code="model_config",
            error_category="upstream_model",
        )

    install_native_run_turn_stub(monkeypatch, fake_run_turn)
    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My father will sponsor me."},
    )

    assert response.status_code == 503
    assert run_count == 1
    detail = response.json()["detail"]
    assert detail["upstream_code"] == "model_config"
    assert "retry_attempts" not in detail

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session_id)
        ).all()
    assert turns == []


def test_message_turn_records_interview_memory_for_answered_question(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_native_run_turn_stub(
        monkeypatch,
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
    install_native_interviewer_stub(monkeypatch)
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
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["assistant_message"] == (
        "你提到想学计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
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
        ("assistant", "native_interviewer_runtime"),
    ]


def test_native_interviewer_runs_when_gate_is_pending_or_waiting_for_parse(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="请说明你的学习计划和毕业后的安排。",
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run for native gate audit")
        ),
    )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert session_resp.status_code == 201
    pending_session_id = session_resp.json()["session_id"]

    pending_response = client.post(
        f"/v1/sessions/{pending_session_id}/messages",
        json={"role": "user", "content": "I want to start with my study plan."},
    )

    assert pending_response.status_code == 200
    pending_payload = pending_response.json()
    assert pending_payload["assistant_message"] == "请说明你的学习计划和毕业后的安排。"
    assert pending_payload["selected_public_runtime"] == "native_interviewer"
    assert pending_payload["gate_progress"]["overall_status"] == "pending_documents"

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert session_resp.status_code == 201
    waiting_session_id = session_resp.json()["session_id"]
    with db_session_factory() as db:
        record = db.get(SessionRecord, waiting_session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="waiting_for_parse_audit",
            required_documents=["funding_proof"],
        )
        db.add(
            DocumentRecord(
                document_id="doc-waiting-native-gate-audit",
                session_id=waiting_session_id,
                filename="funding-proof.pdf",
                status="uploaded",
                artifact_json={
                    "status": "uploaded",
                    "filename": "funding-proof.pdf",
                    "document_type": "funding_proof",
                    "metadata": {
                        "document_type": "funding_proof",
                        "document_assessment": {
                            "document_type": "funding_proof",
                            "document_type_candidates": ["funding_proof"],
                            "counts_toward_gate": True,
                        },
                    },
                },
            )
        )
        db.add(record)
        db.commit()

    waiting_response = client.post(
        f"/v1/sessions/{waiting_session_id}/messages",
        json={"role": "user", "content": "The file is uploaded; continue."},
    )

    assert waiting_response.status_code == 200
    waiting_payload = waiting_response.json()
    assert waiting_payload["assistant_message"] == "请说明你的学习计划和毕业后的安排。"
    assert waiting_payload["selected_public_runtime"] == "native_interviewer"
    assert waiting_payload["gate_progress"]["overall_status"] == "waiting_for_parse"

    with db_session_factory() as db:
        assistant_sources = [
            turn.source
            for turn in db.scalars(
                select(SessionTurnRecord)
                .where(
                    SessionTurnRecord.session_id.in_(
                        [pending_session_id, waiting_session_id]
                    ),
                    SessionTurnRecord.role == "assistant",
                )
                .order_by(SessionTurnRecord.turn_index)
            )
        ]

    assert assistant_sources == [
        "native_interviewer_runtime",
        "native_interviewer_runtime",
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
    enable_admin_user_model_config(db_session_factory)

    def fake_run_turn(self, record, message_text: str) -> dict:
        captured_configs.append(current_user_model_config())
        return {
            "assistant_message": "Which school will you attend?",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
        }

    install_native_run_turn_stub(monkeypatch, fake_run_turn)

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

    install_native_run_turn_stub(monkeypatch, fake_run_turn)

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
    assert "event: debug_event" in body
    assert "event: final" in body
    events = parse_sse_events(body)
    debug_events = [payload for event, payload in events if event == "debug_event"]
    assert any(item["step"] == "message_service.handle_user_turn" for item in debug_events)
    assert events[-1][1]["assistant_message"] == "What will you study?"


def test_messages_stream_model_error_exposes_public_cause(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    run_count = 0

    def fake_run_turn(self, record, message_text, user_turn=None):
        nonlocal run_count
        run_count += 1
        raise ModelRuntimeError(
            detail="上游模型请求超时，本轮面谈回复未生成。",
            status_code=504,
            provider="openai_compatible",
            model="gpt-5.4",
            upstream_code="upstream_timeout",
            error_category="upstream_timeout",
        )

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with caplog.at_level("WARNING"):
        with client.stream(
            "POST",
            f"/v1/sessions/{session_id}/messages/stream",
            json={"role": "user", "content": "My parents live in Shanghai."},
        ) as response:
            body = response.read().decode()

    assert response.status_code == 200
    assert run_count == 3
    events = parse_sse_events(body)
    debug_events = [payload for event, payload in events if event == "debug_event"]
    retry_events = [
        payload for payload in debug_events if payload["step"] == "provider_runtime_retry"
    ]
    assert any(
        payload["status"] == "failed"
        and payload["payload"]["attempt"] == 1
        and payload["payload"]["will_retry"] is True
        for payload in retry_events
    )
    assert events[-1][0] == "error"
    assert events[-1][1] == {
        "status": 504,
        "detail": "上游模型请求超时，本轮面谈回复未生成。",
        "error_category": "upstream_timeout",
        "upstream_code": "upstream_timeout",
        "provider": "openai_compatible",
        "model": "gpt-5.4",
        "retry_attempts": 2,
        "retry_exhausted": True,
    }
    assert any(
        record.message == "message model runtime failed"
        and getattr(record, "error_category", None) == "upstream_timeout"
        and getattr(record, "session_id", None) == session_id
        for record in caplog.records
    )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session_id)
        ).all()
    assert turns == []


def test_messages_stream_requires_switch_for_user_model_config(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_admin_user_model_config(db_session_factory)
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
    enable_admin_user_model_config(db_session_factory)
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

    install_native_run_turn_stub(monkeypatch, fake_run_turn)

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
    assert "event: debug_event" in body
    assert "event: final" in body
    events = parse_sse_events(body)
    event_names = [event for event, _data in events]
    assert event_names[:2] == ["accepted", "debug_event"]
    assert "analyzing" in event_names
    assert event_names[-1] == "final"
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
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？",
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
    event_names = [event for event, _data in events]
    assert event_names[:2] == ["accepted", "debug_event"]
    assert "analyzing" in event_names
    assert event_names[-1] == "final"
    final_payload = events[-1][1]
    assert final_payload["assistant_message"] == (
        "你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
    assert final_payload["agent_runtime"] == "native_interviewer"
    assert final_payload["selected_public_runtime"] == "native_interviewer"
    assert (
        final_payload["runtime_execution"]["execution_runtime"]
        == "native_interviewer_runtime"
    )
    assert "shadow_runtime" not in final_payload["runtime_execution"]
    assert "shadow_run_id" not in final_payload["runtime_execution"]
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
    assert assistant_turn.source == "native_interviewer_runtime"
    assert assistant_turn.metadata_json.get("graph_shadow") is None


def test_message_turn_keeps_family_selection_gate_before_interview_runtime(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("未选择签证家族前不应进入 native interviewer runtime")
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
    assert payload["assistant_message"] == (
        "请先选择签证家族，这样我才能按对应签证场景开始面签问答。"
    )
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
            "请先选择签证家族，这样我才能按对应签证场景开始面签问答。",
            "gate_runtime_service",
        ),
    ]


def test_message_turn_legacy_config_uses_native_interviewer_output(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "legacy")
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="What is the purpose of your travel?",
        decision="continue_interview",
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run for public messages")
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
    assert payload["agent_runtime"] == "native_interviewer"
    assert_native_canonical_runtime_execution(payload, configured_runtime="legacy")
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
        assert record.current_focus_json == {
            "owner": "native_interviewer",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        }
        assert record.interviewer_state_json["owner"] == "native_interviewer_runtime"
        assert record.interviewer_state_json["selected_public_runtime"] == "native_interviewer"


def test_message_turn_graph_shadow_uses_native_public_response_and_single_assistant_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？",
    )

    def legacy_must_not_run(self, record, message_text: str) -> dict:
        raise AssertionError("legacy runtime should not run in graph_shadow mode")

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
    assert payload["assistant_message"] == (
        "你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph_shadow",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "message_turn",
        "fail_open_to_legacy": False,
        "compatibility_runtime_label": "graph_shadow",
    }
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
        ("assistant", "native_interviewer_runtime"),
    ]
    assert record is not None
    assert record.interviewer_state_json["owner"] == "native_interviewer_runtime"
    assert record.interviewer_state_json["runtime_execution"] == payload[
        "runtime_execution"
    ]
    assert turns[1].metadata_json.get("graph_shadow") is None
    assert turns[1].metadata_json["agent_runtime"] == "native_interviewer"
    assert turns[1].metadata_json["selected_public_runtime"] == "native_interviewer"
    assert turns[1].metadata_json["runtime_execution"] == payload[
        "runtime_execution"
    ]


def test_message_turn_graph_shadow_skips_shadow_runtime_and_keeps_native_response(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.GraphRuntimeAdapter.run_turn",
        lambda self, record, message_text, user_turn=None: (_ for _ in ()).throw(
            RuntimeError("shadow exploded")
        ),
    )
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？",
    )

    def legacy_must_not_run(self, record, message_text: str) -> dict:
        raise AssertionError("legacy runtime should not run in graph_shadow mode")

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
    assert payload["assistant_message"] == (
        "你选择计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert (
        payload["runtime_execution"]["execution_runtime"]
        == "native_interviewer_runtime"
    )
    assert "shadow_runtime" not in payload["runtime_execution"]
    assert "shadow_run_id" not in payload["runtime_execution"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "native_interviewer_runtime"),
    ]
    assert turns[1].metadata_json.get("graph_shadow") is None


def test_message_turn_graph_mode_writes_public_response_and_single_assistant_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_native_interviewer_stub(monkeypatch)

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
    assert payload["assistant_message"] == (
        "你提到想学计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["native_run_id"].startswith("native-run-")
    assert "graph_run_id" not in payload
    assert "graph_trace" not in payload
    assert "graph_events" not in payload
    assert "graph_runtime_error" not in payload
    assert payload["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert payload["prompt_trace"]["native_run_id"] == payload["native_run_id"]
    assert payload["prompt_trace"]["prompt_pack_id"] == "ds160.native_interviewer"
    assert payload["runtime_view_state"]["source_turn_id"]
    assert payload["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "message_turn",
        "fail_open_to_legacy": False,
        "compatibility_runtime_label": "graph",
    }

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert [(turn.role, turn.source) for turn in turns] == [
        ("user", "user_message"),
        ("assistant", "native_interviewer_runtime"),
    ]
    assistant_turn = turns[1]
    metadata = assistant_turn.metadata_json
    assert metadata["agent_runtime"] == "native_interviewer"
    assert metadata["selected_public_runtime"] == "native_interviewer"
    assert metadata["runtime_execution"] == payload["runtime_execution"]
    assert metadata["native_run_id"] == payload["native_run_id"]
    assert metadata["runtime_view_state"]["source_turn_id"] == assistant_turn.turn_id
    assert metadata["runtime_view_state"]["prompt_trace"]["native_run_id"] == payload["native_run_id"]
    assert metadata["turn_record"]["assistant_turn_id"] == assistant_turn.turn_id
    assert metadata["turn_record"]["user_turn_id"] == turns[0].turn_id
    assert "graph_events" not in metadata or metadata["graph_events"] is None
    assert record is not None
    assert record.current_governor_decision == "continue_interview"
    assert record.interviewer_state_json["owner"] == "native_interviewer_runtime"
    assert record.interviewer_state_json["selected_public_runtime"] == "native_interviewer"
    assert record.interviewer_state_json["runtime_execution"] == payload["runtime_execution"]



def test_message_turn_legacy_config_is_ignored_and_uses_native_canonical_runtime(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "legacy")
    install_native_interviewer_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run for public messages")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert_native_canonical_runtime_execution(payload, configured_runtime="legacy")


def test_message_turn_native_interviewer_runtime_reports_native_label(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")
    install_native_interviewer_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run in native interviewer mode")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "native_interviewer",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "message_turn",
        "fail_open_to_legacy": False,
    }

    with db_session_factory() as db:
        assistant_turn = db.scalar(
            select(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
            .order_by(SessionTurnRecord.turn_index)
        )
        record = db.get(SessionRecord, session_id)

    assert assistant_turn is not None
    assert assistant_turn.source == "native_interviewer_runtime"
    assert assistant_turn.metadata_json["agent_runtime"] == "native_interviewer"
    assert assistant_turn.metadata_json["runtime_execution"] == payload["runtime_execution"]
    assert record is not None
    assert record.interviewer_state_json["owner"] == "native_interviewer_runtime"
    assert record.interviewer_state_json["selected_public_runtime"] == "native_interviewer"
    assert record.interviewer_state_json["runtime_execution"] == payload["runtime_execution"]




@pytest.mark.parametrize(
    "configured_runtime",
    ["native_interviewer", "graph", "graph_shadow", "graph_canary"],
)
def test_message_turn_public_native_paths_do_not_call_pydantic_ai_model_build(
    client: TestClient,
    db_session_factory,
    monkeypatch,
    configured_runtime: str,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", configured_runtime)
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 0)
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.invalid/v1")

    def pydantic_model_build_must_not_run(self, *args, **kwargs):
        raise AssertionError(
            "public native runtime paths must not build pydantic-ai chat models"
        )

    monkeypatch.setattr(
        "app.agents.model_factory.AgentModelFactory.build",
        pydantic_model_build_must_not_run,
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAIAgentsInterviewerRunner.run",
        lambda self, **kwargs: NativeInterviewerOutput(
            assistant_message="What is your study plan?",
            decision="continue_interview",
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["execution_runtime"] == "native_interviewer_runtime"
    assert payload["runtime_execution"]["runtime_role"] == "canonical"
    assert payload["runtime_execution"]["canonical"] is True


def test_message_turn_native_interviewer_does_not_eager_init_legacy_runtime(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")
    install_native_interviewer_stub(monkeypatch)

    def legacy_init_must_not_run(self, db) -> None:
        raise AssertionError("legacy runtime should not initialize in native mode")

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.__init__",
        legacy_init_must_not_run,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["canonical_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["runtime_role"] == "canonical"
    assert payload["runtime_execution"]["canonical"] is True


def test_message_turn_graph_canary_hundred_percent_uses_native_compat_alias(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_canary")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 100)
    install_native_interviewer_stub(monkeypatch)
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
    payload = response.json()
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["configured_runtime"] == "graph_canary"
    assert payload["runtime_execution"]["compatibility_runtime_label"] == "graph_canary"
    assert payload["runtime_execution"]["execution_runtime"] == "native_interviewer_runtime"
    assert payload["runtime_execution"]["canonical_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["runtime_role"] == "canonical"
    assert payload["runtime_execution"]["canonical"] is True

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
    assert assistant_turn.source == "native_interviewer_runtime"


def test_message_turn_graph_canary_zero_percent_still_uses_native_compat_alias(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_canary")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 0)
    install_native_interviewer_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run when graph_canary misses")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["configured_runtime"] == "graph_canary"
    assert payload["runtime_execution"]["compatibility_runtime_label"] == "graph_canary"
    assert payload["runtime_execution"]["execution_runtime"] == "native_interviewer_runtime"
    assert payload["runtime_execution"]["canonical_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["runtime_role"] == "canonical"
    assert payload["runtime_execution"]["canonical"] is True

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
    assert assistant_turn.source == "native_interviewer_runtime"


def test_message_turn_native_failure_does_not_fail_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        lambda self, record, message_text, user_turn=None: (_ for _ in ()).throw(
            RuntimeError("native exploded")
        ),
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run after native failure")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    with pytest.raises(RuntimeError, match="native exploded"):
        client.post(
            f"/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "I will study computer science."},
        )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert turns == []


def test_message_turn_graph_failure_does_not_fail_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        lambda self, record, message_text, user_turn=None: (_ for _ in ()).throw(
            RuntimeError("native exploded")
        ),
    )
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run after native failure")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    with pytest.raises(RuntimeError, match="native exploded"):
        client.post(
            f"/v1/sessions/{session_id}/messages",
            json={"role": "user", "content": "I will study computer science."},
        )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert turns == []


def test_message_turn_graph_native_missing_model_returns_503_without_canned_fallback(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: (_ for _ in ()).throw(
            AssertionError("legacy runtime should not run when native model is missing")
        ),
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["error_category"] == "model_config"
    assert "OPENAI_API_KEY" in detail["detail"]
    assert "OPENAI_BASE_URL" in detail["detail"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert turns == []


def test_message_turn_deletes_user_turn_when_native_quality_guard_blocks_output(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    run_count = 0

    def fake_run_turn(self, record, message_text, user_turn=None):
        nonlocal run_count
        run_count += 1
        raise ModelRuntimeError(
            detail="模型输出未通过连续面谈质量检查，已阻止发送重复或失真的面试问题。",
            status_code=503,
            upstream_code="native_quality_guard_failed",
        )

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I chose NYU because the program strengthens my AI engineering skills before I return to China.",
            "client_message_id": "client-quality-guard-1",
        },
    )

    assert response.status_code == 503
    assert run_count == 1
    detail = response.json()["detail"]
    assert detail["upstream_code"] == "native_quality_guard_failed"
    assert "质量检查" in detail["detail"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    # Incomplete user turn is cleaned up so the session is not stuck on 409.
    assert turns == []

    # A new client_message_id can continue after quality-guard failure.
    install_native_fixed_interview_response(
        monkeypatch,
        decision="continue_interview",
        assistant_message="这个项目和你毕业后的计划具体怎么衔接？",
    )
    retry = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I chose NYU because the program strengthens my AI engineering skills before I return to China.",
            "client_message_id": "client-quality-guard-2",
        },
    )
    assert retry.status_code == 200
    assert retry.json()["assistant_message"] == (
        "这个项目和你毕业后的计划具体怎么衔接？"
    )


def test_get_messages_returns_public_transcript(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with db_session_factory() as db:
        db.add(
            SessionTurnRecord(
                turn_id="turn-public-user-1",
                turn_index=1,
                session_id=session_id,
                role="user",
                content="I will study computer science at NYU.",
                source="user_message",
                client_message_id="client-public-user-1",
                metadata_json={"phase_state": "interview"},
            )
        )
        db.add(
            SessionTurnRecord(
                turn_id="turn-public-assistant-2",
                turn_index=2,
                session_id=session_id,
                role="assistant",
                content="Why is this NYU program useful for your plan?",
                source="native_interviewer_runtime",
                metadata_json={
                    "phase_state": "interview",
                    "public_reasoning": {
                        "basis": "continue_interview",
                    },
                },
            )
        )
        db.add(
            SessionTurnRecord(
                turn_id="turn-private-system-3",
                turn_index=3,
                session_id=session_id,
                role="system",
                content="internal note",
                source="internal",
                metadata_json={},
            )
        )
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/messages")

    assert response.status_code == 200
    payload = response.json()
    assert payload["session_id"] == session_id
    assert [
        (message["turn_id"], message["role"], message["content"])
        for message in payload["messages"]
    ] == [
        (
            "turn-public-user-1",
            "user",
            "I will study computer science at NYU.",
        ),
        (
            "turn-public-assistant-2",
            "assistant",
            "Why is this NYU program useful for your plan?",
        ),
    ]
    assert payload["messages"][0]["client_message_id"] == "client-public-user-1"
    assert payload["messages"][1]["client_message_id"] is None


def test_messages_stream_graph_mode_keeps_sse_contract(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_native_interviewer_stub(monkeypatch)

    session_id = seed_ready_for_interview_session(client, db_session_factory)
    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/messages/stream",
        json={"role": "user", "content": "I will study computer science."},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    events = parse_sse_events(body)
    event_names = [event for event, _data in events]
    assert event_names[:2] == ["accepted", "debug_event"]
    assert "analyzing" in event_names
    assert event_names[-1] == "final"
    final_payload = events[-1][1]
    assert final_payload["agent_runtime"] == "native_interviewer"
    assert final_payload["selected_public_runtime"] == "native_interviewer"
    assert final_payload["runtime_execution"]["configured_runtime"] == "graph"
    assert (
        final_payload["runtime_execution"]["execution_runtime"]
        == "native_interviewer_runtime"
    )
    assert final_payload["runtime_execution"]["canonical_runtime"] == "native_interviewer"
    assert final_payload["runtime_execution"]["runtime_role"] == "canonical"
    assert final_payload["runtime_execution"]["canonical"] is True
    assert final_payload["assistant_message"] == (
        "你提到想学计算机方向。这个项目和你毕业后的计划具体怎么衔接？"
    )
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
    detail = response.json()["detail"]
    assert detail["error_category"] == "model_config"
    assert "OPENAI_API_KEY" in detail["detail"]
    assert "OPENAI_BASE_URL" in detail["detail"]

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


def test_native_message_turn_persists_turn_record_on_assistant_turn_metadata(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_native_interviewer_stub(
        monkeypatch,
        assistant_message="What is the purpose of your travel?",
        decision="continue_interview",
    )

    session_id = seed_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    assert_native_canonical_runtime_execution(response.json())

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
    assert turn_record["trace_refs"] == ["native_interviewer"]
    assert assistant_turn.metadata_json["requested_documents"] == []
    assert assistant_turn.metadata_json["turn_decision"] == "continue_interview"
    assert runtime_view_state["source_turn_id"] == assistant_turn.turn_id
    assert runtime_view_state["decision"] == "continue_interview"
    assert runtime_view_state["current_key_question"] == (
        "What is the purpose of your travel?"
    )


def test_runtime_view_sync_does_not_restore_original_stale_document_request() -> None:
    service = MessageService.__new__(MessageService)
    service.session_read_model = SimpleNamespace(
        build_from_record=lambda record: SimpleNamespace(
            phase_state="interview",
            runtime_view_state=RuntimeViewState(
                source_turn_id="turn-assistant-new",
                source_turn_content=(
                    "How does this program fit your future plan?"
                ),
                decision="continue_interview",
                governor_decision="continue_interview",
                public_status="continue_interview",
                current_focus={
                    "kind": "interview_question",
                    "question": "How does this program fit your future plan?",
                },
                current_key_question=(
                    "How does this program fit your future plan?"
                ),
                current_key_proof=None,
                requested_documents=[],
                remaining_required_documents=["funding_proof"],
                advisory_context={"missing_evidence": ["funding_proof"]},
            ),
        )
    )
    record = SimpleNamespace(
        phase_state="interview",
        current_focus_json={
            "kind": "interview_question",
            "question": "How does this program fit your future plan?",
        },
    )
    response = {
        "assistant_message": "How does this program fit your future plan?",
        "governor_decision": "continue_interview",
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
        "turn_decision": {
            "decision": "need_more_evidence",
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
            "current_key_proof": "funding_proof",
        },
        "runtime_view_state": {
            "source_turn_id": "old-turn",
            "decision": "need_more_evidence",
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
            "current_key_proof": "funding_proof",
        },
        "advisory_context": {"missing_evidence": ["funding_proof"]},
        "document_review": {},
        "prompt_trace": {},
        "agent_runtime": "graph",
        "selected_public_runtime": "native_interviewer",
        "runtime_execution": {},
    }
    assistant_turn = SimpleNamespace(
        turn_id="turn-assistant-new",
        content="How does this program fit your future plan?",
        metadata_json={},
    )

    service._sync_runtime_view_contract(record, response, assistant_turn)

    runtime_view_state = assistant_turn.metadata_json["runtime_view_state"]
    assert response["requested_documents"] == []
    assert response["remaining_required_documents"] == ["funding_proof"]
    assert response["turn_decision"]["decision"] == "continue_interview"
    assert response["turn_decision"]["requested_documents"] == []
    assert response["turn_decision"]["remaining_required_documents"] == [
        "funding_proof"
    ]
    assert response["turn_decision"]["current_key_proof"] is None
    assert runtime_view_state["source_turn_id"] == assistant_turn.turn_id
    assert runtime_view_state["source_turn_content"] == assistant_turn.content
    assert runtime_view_state["requested_documents"] == []
    assert runtime_view_state["remaining_required_documents"] == ["funding_proof"]
    assert runtime_view_state["advisory_context"]["missing_evidence"] == [
        "funding_proof"
    ]
    assert runtime_view_state["current_key_proof"] is None




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
    install_native_run_turn_stub(
        monkeypatch,
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
    install_native_run_turn_stub(
        monkeypatch,
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
    install_native_run_turn_stub(
        monkeypatch,
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
    install_native_run_turn_stub(
        monkeypatch,
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
    install_native_run_turn_stub(
        monkeypatch,
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
                    # Gate ready requires understanding when
                    # material_understanding_required=True (default).
                    "understanding_status": "completed",
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
    install_native_fixed_interview_response(
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
def test_refusal_session_cannot_continue_with_new_messages(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_native_fixed_interview_response(
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
def test_negated_fraud_statement_does_not_trigger_refusal(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_native_fixed_interview_response(
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
