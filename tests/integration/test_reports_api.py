from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.domain.runtime import build_initial_gate_status
from app.repositories.session_turn_repo import SessionTurnRepository
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'reports-api.sqlite3'}",
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


def test_user_report_returns_summary_shape(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    assert "outcome_label" in response.json()
    assert "interview_status" in response.json()


def test_reports_api_returns_gate_review_copy_and_internal_histories(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        gate_status = build_initial_gate_status(
            declared_family="f1",
            scenario_key="parent_sponsored",
            required_documents=["funding_proof"],
        )
        gate_status["status"] = "waiting_for_parse"
        record.phase_state = "gate_review"
        record.current_governor_decision = "need_more_evidence"
        record.profile_json = {"funding": {"primary_source": "parents"}}
        record.gate_status_json = gate_status
        record.runtime_trace_json = [
            {"node_name": "resolve_evidence", "summary": "documented_refs=0"}
        ]
        record.score_history_json = [
            {
                "scoring_stage": "gate_review",
                "category_fit": 0,
                "document_readiness": 40,
                "narrative_consistency": 0,
                "confidence": 0,
                "missing_evidence": ["funding_proof"],
                "risk_flags": [],
                "summary": "missing=1 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "need_more_evidence",
                "summary": "decision=need_more_evidence",
            }
        ]
        db.add(record)
        db.commit()

    user_response = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_response = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert user_response.status_code == 200
    assert user_response.json()["interview_status"] == "waiting_key_proof"
    assert user_response.json()["outcome_label"] == "补件审核中"
    assert (
        user_response.json()["summary"]
        == "当前处于材料门控阶段。材料已提交，仍在解析中，暂不能进入正式 interview。"
    )

    assert internal_response.status_code == 200
    internal_payload = internal_response.json()
    assert internal_payload["runtime_trace"] == [
        {"node_name": "resolve_evidence", "summary": "documented_refs=0"}
    ]
    assert internal_payload["score_history"] == [
        {
            "scoring_stage": "gate_review",
            "category_fit": 0,
            "document_readiness": 40,
            "narrative_consistency": 0,
            "confidence": 0,
            "missing_evidence": ["funding_proof"],
            "risk_flags": [],
            "summary": "missing=1 risk_flags=0",
        }
    ]
    assert internal_payload["governor_history"] == [
        {
            "decision": "need_more_evidence",
            "summary": "decision=need_more_evidence",
        }
    ]
    assert internal_payload["runtime_ledger"]["session_id"] == session_id
    assert internal_payload["runtime_ledger"]["turns"] == []
    assert [event["event_type"] for event in internal_payload["runtime_ledger"]["events"]] == [
        "trace",
        "scorer",
        "boundary",
    ]
    assert internal_payload["runtime_ledger"]["events"][0]["event_id"].startswith(
        "session-orphan:trace:"
    )


def test_reports_api_returns_interview_copy(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        gate_status = build_initial_gate_status(
            declared_family="f1",
            scenario_key="parent_sponsored",
            required_documents=["funding_proof"],
        )
        gate_status["status"] = "ready_for_interview"
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.profile_json = {"funding": {"primary_source": "self"}}
        record.gate_status_json = gate_status
        record.runtime_trace_json = [
            {"node_name": "build_next_action", "summary": "requested_documents=0"}
        ]
        record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 78,
                "document_readiness": 82,
                "narrative_consistency": 75,
                "confidence": 80,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "continue_interview"
    assert payload["outcome_label"] == "正式问答进行中"
    assert payload["summary"] == "当前已进入正式 interview 阶段，可继续回答后续问题。"
    assert payload["recommended_improvements"] == [
        "继续回答后续问题，并保持叙事一致。",
    ]


def test_user_report_can_derive_runtime_view_state_from_ledger_when_state_is_empty(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.profile_json = {"funding": {"primary_source": "self"}}
        record.interviewer_state_json = {}
        record.current_focus_json = {}
        record.runtime_trace_json = [
            {
                "node_name": "turn_decision",
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
                "provider": "openai",
                "model": "gpt-5.4",
                "metadata": {"reasoning_effort": "high"},
                "turn_decision": "continue_interview",
            }
        ]
        record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 78,
                "document_readiness": 82,
                "narrative_consistency": 75,
                "confidence": 80,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_id,
            content="I want to study computer science.",
            source="user_message",
            commit=False,
        )
        repo.append_assistant_turn(
            session_id=session_id,
            content="What is the purpose of your travel?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {
                        "kind": "interview_question",
                        "question": "What is the purpose of your travel?",
                    },
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
            commit=False,
        )
        db.add(record)
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_response = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert response.status_code == 200
    payload = response.json()
    assert payload["interview_status"] == "continue_interview"
    assert payload["current_key_question"] == "What is the purpose of your travel?"
    assert payload["allowed_next_actions"] == [
        "answer_question",
        "continue_interview",
    ]
    assert payload["prompt_trace"] == {
        "prompt_pack_id": "ds160.interviewer",
        "prompt_version": "v2",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }

    assert internal_response.status_code == 200
    assert internal_response.json()["runtime_view_state"]["current_key_question"] == (
        "What is the purpose of your travel?"
    )


def test_reports_api_distinguishes_high_risk_review_from_simulated_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    high_risk_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    high_risk_session_id = high_risk_resp.json()["session_id"]
    refusal_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    refusal_session_id = refusal_resp.json()["session_id"]

    with db_session_factory() as db:
        high_risk_record = db.get(SessionRecord, high_risk_session_id)
        refusal_record = db.get(SessionRecord, refusal_session_id)
        assert high_risk_record is not None
        assert refusal_record is not None

        for record, decision in (
            (high_risk_record, "high_risk_review"),
            (refusal_record, "simulated_refusal"),
        ):
            record.phase_state = "interview"
            record.current_governor_decision = decision
            record.profile_json = {"funding": {"primary_source": "self"}}
            db.add(record)
        db.commit()

    high_risk_response = client.get(f"/v1/sessions/{high_risk_session_id}/reports/user")
    refusal_response = client.get(f"/v1/sessions/{refusal_session_id}/reports/user")

    assert high_risk_response.status_code == 200
    assert refusal_response.status_code == 200
    assert high_risk_response.json()["interview_status"] == "high_risk_review"
    assert high_risk_response.json()["outcome_label"] == "高风险待复核"
    assert refusal_response.json()["interview_status"] == "simulated_refusal"
    assert refusal_response.json()["outcome_label"] == "模拟拒签结果"
