from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.domain.runtime import build_initial_gate_status
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
    assert user_response.json()["outcome_label"] == "补件审核中"
    assert user_response.json()["summary"] == "材料已提交，仍在解析中，暂不能进入正式 interview。"

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
    assert payload["outcome_label"] == "正式问答进行中"
    assert payload["summary"] == "当前已进入正式 interview，可继续回答后续问题。"
    assert payload["recommended_improvements"] == [
        "继续回答后续问题，并保持叙事一致。",
    ]
