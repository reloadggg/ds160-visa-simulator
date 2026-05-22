from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.main import app
from app.agents.schemas import InterviewNextAction


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sessions-api.sqlite3'}",
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


def test_create_session_returns_initial_phase(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["phase_state"] == "intake"
    assert payload["current_governor_decision"] == "need_more_evidence"
    assert payload["gate_status"] == {
        "declared_family": "f1",
        "scenario_key": "parent_sponsored",
        "status": "pending_documents",
        "required_documents": [
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


def test_create_session_uses_family_default_scenario(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "j1"})

    assert response.status_code == 201
    assert response.json()["gate_status"] == {
        "declared_family": "j1",
        "scenario_key": "institution_funded",
        "status": "pending_documents",
        "required_documents": [
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
                "document_type": "ds2019",
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


def test_create_session_persists_runtime_state_skeleton(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    session_id = response.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.gate_status_json["status"] == "pending_documents"
    assert [doc["document_type"] for doc in record.gate_status_json["required_documents"]] == [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
    ]
    assert record.runtime_trace_json == []
    assert record.score_history_json == []
    assert record.governor_history_json == []


def test_create_session_without_declared_family_keeps_stable_gate_shape(
    client: TestClient,
) -> None:
    response = client.post("/v1/sessions", json={"declared_family": None})

    assert response.status_code == 201
    payload = response.json()
    assert payload["gate_status"] == {
        "declared_family": None,
        "scenario_key": None,
        "status": "family_not_selected",
        "required_documents": [],
    }


def test_required_package_endpoint_uses_declared_family(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 200
    assert response.json()["required_initial_package"] == [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
    ]


def test_required_package_endpoint_supports_non_f1_family(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "j1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 200
    assert response.json()["required_initial_package"] == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
    ]


def test_required_package_rejects_unlocked_family(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": None})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 409
    assert response.json()["detail"] == "declared_family not locked"


def test_create_session_rejects_unsupported_family(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "zzz"})

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported declared_family: zzz"


def test_required_package_rejects_invalid_stored_family(
    client: TestClient,
    db_session_factory,
) -> None:
    with db_session_factory() as db:
        db.add(SessionRecord(session_id="sess-bad", declared_family="zzz"))
        db.commit()

    response = client.get("/v1/sessions/sess-bad/required-package")

    assert response.status_code == 409
    assert response.json()["detail"] == "unsupported declared_family: zzz"


def test_debug_fill_current_gap_creates_relationship_proof(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="What is the purpose of your travel?",
            requested_documents=[],
            decision="continue_interview",
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.current_focus_json = {
            "kind": "required_document",
            "document_type": "relationship_proof_between_applicant_and_sponsors",
        }
        db.add(record)
        db.commit()

    response = client.post(f"/v1/sessions/{session_id}/debug/fill-current-gap")

    assert response.status_code == 200
    payload = response.json()
    assert payload["filled_document_type"] == "relationship_proof_between_applicant_and_sponsors"
    assert payload["document_id"].startswith("doc-")
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["governor_decision"] == "continue_interview"
    assert payload["turn_decision"]["decision"] == "continue_interview"
    assert payload["runtime_view_state"]["decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.current_governor_decision == "continue_interview"
        assert record.current_focus_json == {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "What is the purpose of your travel?",
        }
        assert record.profile_json["funding"]["sponsor_relationship"] == "parents"
        assert record.profile_json["family_specific"]["parent_names"] == "LI WEIGUO; ZHANG HUI"

        evidence = db.query(EvidenceItemRecord).filter_by(session_id=session_id).all()

    assert {item.field_path for item in evidence} >= {
        "/funding/sponsor_relationship",
        "/family/parent_names",
    }


def test_debug_fill_current_gap_supports_normal_school_data(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please explain why you chose this university.",
            requested_documents=[],
            decision="continue_interview",
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.profile_json = {
            "profile_id": f"profile-{session_id}",
            "profile_version": 1,
            "identity": {"full_name": "LI, MINGHAO"},
            "visa_intent": {"declared_family": "f1"},
            "education": {
                "school_name": "New York University",
                "program_name": "Master of Science in Computer Science",
                "sevis_id": "N0034567890",
            },
            "funding": {},
            "ds160_view": {},
            "field_states": {},
            "field_provenance": {},
        }
        record.current_focus_json = {
            "kind": "required_document",
            "document_type": "i20",
        }
        db.add(record)
        db.commit()

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fill_scenario"] == "normal"
    assert payload["filled_document_type"] == "i20"
    assert "正常材料" in payload["fill_scenario_label"]
    assert payload["assistant_message"] == "Please explain why you chose this university."

    with db_session_factory() as db:
        document = db.query(DocumentRecord).filter_by(session_id=session_id).one()
        evidence = db.query(EvidenceItemRecord).filter_by(
            session_id=session_id,
            field_path="/education/school_name",
        ).one()

    assert "School name: New York University" in document.raw_text
    assert evidence.value == "New York University"


def test_debug_fill_current_gap_supports_sponsor_equity_gap(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please explain the source of your parents' funds.",
            requested_documents=[],
            decision="continue_interview",
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "sponsor_equity_gap"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fill_scenario"] == "sponsor_equity_gap"
    assert payload["filled_document_type"] == "funding_proof"
    assert "股权" in payload["fill_scenario_label"]

    with db_session_factory() as db:
        evidence = db.query(EvidenceItemRecord).filter_by(
            session_id=session_id,
            field_path="/funding/equity_ownership",
        ).one()

    assert "38% shares" in evidence.value


def test_debug_fill_current_gap_normalizes_chinese_remaining_document_text(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Please continue.",
            requested_documents=[],
            decision="continue_interview",
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.interviewer_state_json = {
            "remaining_required_documents": [
                "如申请人坚持最终入读纽约大学，请提供与纽约大学对应的最新 I-20 或录取材料"
            ]
        }
        db.add(record)
        db.commit()

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    assert response.json()["filled_document_type"] == "i20"


def test_debug_fill_current_gap_rejects_unknown_scenario(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "not-a-scenario"},
    )

    assert response.status_code == 422
    assert "unsupported debug fill scenario" in response.json()["detail"]


def test_debug_fill_current_gap_persists_assistant_refresh_turn(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: InterviewNextAction(
            assistant_message="Now explain your study plan.",
            requested_documents=[],
            decision="continue_interview",
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    assert response.json()["assistant_message"] == "Now explain your study plan."

    with db_session_factory() as db:
        chunk_count = db.query(DocumentChunkRecord).filter_by(session_id=session_id).count()
        persisted_messages = db.query(SessionTurnRecord).filter_by(
            session_id=session_id,
        ).all()

    assert chunk_count >= 1
    assert any(
        turn.role == "assistant" and turn.content == "Now explain your study plan."
        for turn in persisted_messages
    )
