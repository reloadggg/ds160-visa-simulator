from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.core import settings as settings_module
from app.db.session import get_db
from app.main import app


def install_material_refresh_stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_refresh_after_material_change(self, record, *, reason: str) -> dict:
        calls.append(reason)
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "Please continue with your study plan.",
        }
        record.interviewer_state_json = {
            "owner": "interviewer_runtime_service",
            "status": "continue_interview",
            "public_status": "continue_interview",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "next_action": "answer_question",
            "decision_hint": "continue_interview",
            "current_key_question": "Please continue with your study plan.",
            "current_key_proof": None,
            "current_risk_code": None,
            "risk_level": "none",
            "allowed_next_actions": ["answer_question", "continue_interview"],
            "requested_documents": [],
            "remaining_required_documents": [],
            "risk_codes": [],
            "history_turn_count": 0,
            "document_review": {},
            "advisory_context": {
                "score_summary": {},
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
                "missing_evidence_summary": None,
            },
            "prompt_trace": {},
        }
        return {
            "assistant_message": "Please continue with your study plan.",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {
                "decision": "continue_interview",
                "requested_documents": [],
                "remaining_required_documents": [],
                "current_key_question": "Please continue with your study plan.",
            },
            "advisory_context": {
                "score_summary": {},
                "risk_codes": [],
                "missing_evidence": [],
                "risk_level": "none",
                "missing_evidence_summary": None,
            },
            "prompt_trace": {},
            "turn_record": {
                "turn_id": f"{record.session_id}:debug-refresh",
                "session_id": record.session_id,
                "user_input": reason,
                "decision": "continue_interview",
                "assistant_message": "Please continue with your study plan.",
                "requested_documents": [],
                "remaining_required_documents": [],
                "focus": record.current_focus_json,
                "trace_refs": [],
                "artifacts": [],
                "advisory_summary": {
                    "risk_codes": [],
                    "missing_evidence": [],
                    "risk_level": "none",
                },
            },
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.refresh_after_material_change",
        fake_refresh_after_material_change,
    )
    return calls


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
    payload = response.json()
    assert payload["scenario_key"] == "parent_sponsored"
    assert payload["official_pre_interview_required"] == [
        "ds160",
        "passport_bio",
        "i20",
    ]
    assert payload["simulator_recommended_evidence"] == [
        "admission_letter",
        "funding_proof",
    ]
    assert payload["required_initial_package"] == [
        "ds160",
        "passport_bio",
        "i20",
    ]


def test_required_package_endpoint_supports_non_f1_family(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "j1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario_key"] == "institution_funded"
    assert payload["official_pre_interview_required"] == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
    ]
    assert payload["simulator_recommended_evidence"] == []
    assert payload["required_initial_package"] == [
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
    refresh_calls = install_material_refresh_stub(monkeypatch)
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
    assert refresh_calls == [
        "debug_fill:relationship_proof_between_applicant_and_sponsors"
    ]
    assert payload["assistant_message"] == "Please continue with your study plan."
    assert payload["governor_decision"] == "continue_interview"
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["turn_decision"]["decision"] == "continue_interview"
    assert payload["runtime_view_state"]["decision"] == "continue_interview"

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "interview"
        assert record.gate_status_json["status"] == "pending_documents"
        assert record.current_governor_decision == "continue_interview"
        assert record.current_focus_json == {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "Please continue with your study plan.",
        }
        assert record.profile_json["funding"]["sponsor_relationship"] == "parents"
        assert (
            record.profile_json["family_specific"]["parent_names"]
            == "PARENT SPONSOR A; PARENT SPONSOR B"
        )

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
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.profile_json = {
            "profile_id": f"profile-{session_id}",
            "profile_version": 1,
            "identity": {"full_name": "TEST APPLICANT"},
            "visa_intent": {"declared_family": "f1"},
            "education": {
                "school_name": "Example University",
                "program_name": "Example Degree Program",
                "sevis_id": "N0000000000",
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
    assert refresh_calls == ["debug_fill:i20"]
    assert payload["assistant_message"] == "Please continue with your study plan."
    assert payload["requested_documents"] == []

    with db_session_factory() as db:
        document = db.query(DocumentRecord).filter_by(session_id=session_id).one()
        evidence = db.query(EvidenceItemRecord).filter_by(
            session_id=session_id,
            field_path="/education/school_name",
        ).one()

    assert "School name: Example University" in document.raw_text
    assert evidence.value == "Example University"


def test_debug_fill_current_gap_supports_generic_school_mismatch(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.profile_json = {
            "profile_id": f"profile-{session_id}",
            "profile_version": 1,
            "identity": {"full_name": "TEST APPLICANT"},
            "visa_intent": {"declared_family": "f1"},
            "education": {
                "school_name": "Example University",
                "program_name": "Example Degree Program",
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
        json={"scenario": "school_mismatch"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["fill_scenario"] == "school_mismatch"
    assert payload["filled_document_type"] == "i20"
    assert refresh_calls == ["debug_fill:i20"]
    assert payload["assistant_message"] == "Please continue with your study plan."
    assert payload["requested_documents"] == []

    with db_session_factory() as db:
        document = db.query(DocumentRecord).filter_by(session_id=session_id).one()
        evidence = db.query(EvidenceItemRecord).filter_by(
            session_id=session_id,
            field_path="/education/school_name",
        ).one()

    assert "School name: Alternate Example University" in document.raw_text
    assert evidence.value == "Alternate Example University"
    assert evidence.value != "Example University"


def test_debug_fill_current_gap_supports_sponsor_equity_gap(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = install_material_refresh_stub(monkeypatch)
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
    assert refresh_calls == ["debug_fill:funding_proof"]
    assert payload["assistant_message"] == "Please continue with your study plan."
    assert payload["requested_documents"] == []

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
    install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.interviewer_state_json = {
            "remaining_required_documents": [
                "如申请人坚持最终入读目标学校，请提供与目标学校对应的最新 I-20 或录取材料"
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
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    assert refresh_calls == ["debug_fill:ds160"]
    assert response.json()["assistant_message"] == "Please continue with your study plan."

    with db_session_factory() as db:
        chunk_count = db.query(DocumentChunkRecord).filter_by(session_id=session_id).count()
        persisted_messages = db.query(SessionTurnRecord).filter_by(
            session_id=session_id,
        ).all()

    assert chunk_count >= 1
    assert any(
        turn.role == "assistant"
        and turn.source == "interviewer_runtime_service"
        and turn.content == "Please continue with your study plan."
        for turn in persisted_messages
    )


def test_debug_fill_current_gap_graph_runtime_persists_graph_assistant_turn(
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

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "我会继续围绕你的 DS-160 材料做下一步核对。"
    assert payload["turn_decision"]["decision"] == "continue_interview"

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [(turn.role, turn.source) for turn in turns] == [
        ("assistant", "graph_runtime_adapter"),
    ]
    metadata = turns[0].metadata_json
    assert metadata["agent_runtime"] == "graph"
    assert metadata["selected_public_runtime"] == "graph"
    assert metadata["prompt_trace"]["graph_trigger"] == "material_change"
    assert metadata["prompt_trace"]["material_change_reason"] == "material_added:ds160"
    assert metadata["graph_events"][0]["payload"]["trigger"] == "material_change"
    assert (
        metadata["graph_events"][0]["payload"]["material_change_reason"]
        == "material_added:ds160"
    )
    assert metadata["graph_events"][-1]["event_type"] == "final"
    assert metadata["turn_record"].get("user_turn_id") is None
    assert metadata["turn_record"]["user_input"] == "material_added:ds160"
    assert metadata["turn_record"]["assistant_turn_id"] == turns[0].turn_id


def test_debug_fill_current_gap_graph_failure_fails_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", True)
    monkeypatch.setattr(
        "app.services.graph_runtime_adapter.GraphRuntimeAdapter.run_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            RuntimeError("graph material refresh exploded")
        ),
    )
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    assert refresh_calls == ["debug_fill:ds160"]
    assert response.json()["assistant_message"] == "Please continue with your study plan."

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
    assert assistant_turn.source == "interviewer_runtime_service"
    assert assistant_turn.metadata_json["graph_runtime_error"] == {
        "status": "error",
        "agent_runtime": "graph",
        "selected_public_runtime": "graph",
        "error_type": "RuntimeError",
        "error_message": "graph material refresh exploded",
        "fallback_runtime": "legacy",
    }


def test_debug_fill_current_gap_graph_shadow_keeps_legacy_response(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/fill-current-gap",
        json={"scenario": "normal"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert refresh_calls == ["debug_fill:ds160"]
    assert payload["assistant_message"] == "Please continue with your study plan."

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
    assert assistant_turn.source == "interviewer_runtime_service"
    graph_shadow = assistant_turn.metadata_json["graph_shadow"]
    assert graph_shadow["status"] == "completed"
    assert graph_shadow["agent_runtime"] == "graph_shadow"
    assert graph_shadow["prompt_trace"]["graph_trigger"] == "material_change"
    assert graph_shadow["prompt_trace"]["material_change_reason"] == "material_added:ds160"


def test_runtime_trace_endpoint_returns_graph_events(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = {
            "declared_family": "f1",
            "scenario_key": "parent_sponsored",
            "status": "ready_for_interview",
            "required_documents": [],
        }
        db.add(record)
        db.commit()

    message_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert message_response.status_code == 200
    run_id = message_response.json()["graph_run_id"]
    trace_response = client.get(f"/v1/sessions/{session_id}/runtime-traces/{run_id}")

    assert trace_response.status_code == 200
    payload = trace_response.json()
    assert payload["session_id"] == session_id
    assert payload["run_id"] == run_id
    assert payload["agent_runtime"] == "graph"
    assert payload["selected_public_runtime"] == "graph"
    assert payload["graph_trace"]["run_id"] == run_id
    assert payload["graph_events"][0]["event_type"] == "accepted"
    assert payload["graph_events"][-1]["event_type"] == "final"

    missing_response = client.get(
        f"/v1/sessions/{session_id}/runtime-traces/graph-run-missing"
    )
    assert missing_response.status_code == 404
