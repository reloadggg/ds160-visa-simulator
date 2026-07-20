from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import AdminSettingRecord, DocumentRecord, SessionRecord, SessionTurnRecord
from app.core import settings as settings_module
from app.db.session import get_db
from app.main import app
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput


def install_material_refresh_stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_run_material_change(self, record, *, reason: str) -> dict:
        calls.append(reason)
        current_focus = {
            "owner": "native_interviewer_runtime",
            "kind": "interview_question",
            "question": "Please continue with your study plan.",
        }
        advisory_context = {
            "score_summary": {},
            "risk_codes": [],
            "missing_evidence": [],
            "risk_level": "none",
            "missing_evidence_summary": None,
        }
        return {
            "assistant_message": "Please continue with your study plan.",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {
                "decision": "continue_interview",
                "assistant_message_author": "native_interviewer",
                "next_safe_action": "continue_interview",
                "requested_documents": [],
                "remaining_required_documents": [],
                "current_key_question": "Please continue with your study plan.",
            },
            "advisory_context": advisory_context,
            "prompt_trace": {
                "prompt_pack_id": "ds160.native_interviewer",
                "prompt_version": "native-test-stub",
                "native_trigger": "material_change",
                "material_change_reason": reason,
            },
            "document_review": {},
            "runtime_view_state": {
                "decision": "continue_interview",
                "governor_decision": "continue_interview",
                "public_status": "continue_interview",
                "current_focus": current_focus,
                "current_key_question": "Please continue with your study plan.",
                "current_key_proof": None,
                "current_risk_code": None,
                "requested_documents": [],
                "remaining_required_documents": [],
                "allowed_next_actions": ["answer_question", "continue_interview"],
                "risk_level": "none",
                "advisory_context": advisory_context,
                "document_review": {},
                "prompt_trace": {
                    "prompt_pack_id": "ds160.native_interviewer",
                    "prompt_version": "native-test-stub",
                    "native_trigger": "material_change",
                    "material_change_reason": reason,
                },
            },
        }

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_material_change",
        fake_run_material_change,
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


def test_clear_account_sessions_is_noop_without_auth(client: TestClient) -> None:
    response = client.delete("/v1/sessions")

    assert response.status_code == 200
    assert response.json() == {"deleted_count": 0, "remaining_session_id": None}


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
        "material_added:relationship_proof_between_applicant_and_sponsors"
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
            "owner": "native_interviewer_runtime",
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
    assert payload["fill_scenario_label"] == "生成当前缺口参考材料"
    assert refresh_calls == ["material_added:i20"]
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
    assert refresh_calls == ["material_added:i20"]
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
    assert refresh_calls == ["material_added:funding_proof"]
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


def test_debug_fill_current_gap_refreshes_state_without_assistant_turn(
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
    assert refresh_calls == ["material_added:ds160"]
    assert response.json()["assistant_message"] == "Please continue with your study plan."

    with db_session_factory() as db:
        chunk_count = db.query(DocumentChunkRecord).filter_by(session_id=session_id).count()
        persisted_messages = db.query(SessionTurnRecord).filter_by(
            session_id=session_id,
        ).all()
        record = db.get(SessionRecord, session_id)

    assert chunk_count >= 1
    assert [turn.role for turn in persisted_messages] == []
    assert record is not None
    assert record.current_governor_decision == "continue_interview"
    assert record.current_focus_json["question"] == "Please continue with your study plan."


def test_debug_fill_current_gap_graph_runtime_refreshes_state_without_assistant_turn(
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
    assert payload["assistant_message"] == ""
    assert payload["turn_decision"]["decision"] == "continue_interview"
    assert payload["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert payload["material_refresh"]["assistant_turn_created"] is False
    assert payload["material_refresh"]["prompt_trace"]["native_trigger"] == "material_change"
    assert (
        payload["material_refresh"]["prompt_trace"]["material_change_reason"]
        == "material_added:ds160"
    )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert turns == []
    assert record is not None
    material_refresh = record.interviewer_state_json["last_material_refresh"]
    assert material_refresh["agent_runtime"] == "native_interviewer"
    assert material_refresh["selected_public_runtime"] == "native_interviewer"
    assert material_refresh["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "material_change",
        "fail_open_to_legacy": False,
        "compatibility_runtime_label": "graph",
    }
    assert material_refresh["prompt_trace"]["native_trigger"] == "material_change"
    assert (
        material_refresh["prompt_trace"]["material_change_reason"]
        == "material_added:ds160"
    )
    assert "graph_events" not in material_refresh
    assert material_refresh["assistant_turn_created"] is False


def test_debug_fill_current_gap_graph_failure_does_not_fail_open_to_legacy(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            RuntimeError("native material refresh exploded")
        ),
    )
    legacy_refresh_calls: list[str] = []

    def legacy_refresh_must_not_run(self, record, *, reason: str) -> dict:
        legacy_refresh_calls.append(reason)
        raise AssertionError("legacy material refresh should not run after native failure")

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
    assert legacy_refresh_calls == []
    assert payload["assistant_message"] is None
    assert payload["material_refresh"] == {}
    assert payload["main_flow_refresh_error"] == (
        "RuntimeError: native material refresh exploded"
    )

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session_id)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert turns == []
    assert record is not None
    assert "last_material_refresh" not in (record.interviewer_state_json or {})


def test_debug_fill_current_gap_graph_shadow_uses_native_public_refresh(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph_shadow")

    def legacy_refresh_must_not_run(self, record, *, reason: str) -> dict:
        raise AssertionError("legacy material refresh should not run in graph_shadow mode")

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
    assert payload["assistant_message"] == ""
    assert payload["turn_decision"]["decision"] == "continue_interview"
    assert payload["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert payload["material_refresh"]["assistant_turn_created"] is False
    assert payload["material_refresh"]["agent_runtime"] == "native_interviewer"
    assert payload["material_refresh"]["selected_public_runtime"] == "native_interviewer"
    assert payload["material_refresh"]["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph_shadow",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "material_change",
        "fail_open_to_legacy": False,
        "compatibility_runtime_label": "graph_shadow",
    }
    assert (
        payload["material_refresh"]["prompt_trace"]["native_trigger"]
        == "material_change"
    )
    assert (
        payload["material_refresh"]["prompt_trace"]["material_change_reason"]
        == "material_added:ds160"
    )
    assert "graph_shadow" not in payload["material_refresh"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session_id)
        ).all()
        record = db.get(SessionRecord, session_id)

    assert turns == []
    assert record is not None
    material_refresh = record.interviewer_state_json["last_material_refresh"]
    assert material_refresh["agent_runtime"] == "native_interviewer"
    assert material_refresh["selected_public_runtime"] == "native_interviewer"
    assert material_refresh["runtime_execution"] == payload["material_refresh"][
        "runtime_execution"
    ]
    assert material_refresh["prompt_trace"]["native_trigger"] == "material_change"
    assert (
        material_refresh["prompt_trace"]["material_change_reason"]
        == "material_added:ds160"
    )
    assert "graph_shadow" not in material_refresh


def test_runtime_trace_endpoint_returns_graph_events(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
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
            assistant_message="请说明这个项目如何支持你的学习计划。",
            decision="continue_interview",
        ),
    )
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
    run_id = message_response.json()["native_run_id"]
    with db_session_factory() as db:
        db.add(
            AdminSettingRecord(
                setting_key="demo",
                value_json={
                    "debug_console_enabled": False,
                    "debug_material_enabled": False,
                    "show_github_link": False,
                    "wx_entry_enabled": False,
                    "user_model_config_enabled": False,
                    "rag_status_user_visible": False,
                },
            )
        )
        db.commit()
    disabled_response = client.get(f"/v1/sessions/{session_id}/runtime-traces/{run_id}")
    assert disabled_response.status_code == 403

    with db_session_factory() as db:
        db.merge(
            AdminSettingRecord(
                setting_key="demo",
                value_json={
                    "debug_console_enabled": True,
                    "debug_material_enabled": False,
                    "show_github_link": False,
                    "wx_entry_enabled": False,
                    "user_model_config_enabled": False,
                    "rag_status_user_visible": False,
                },
            )
        )
        db.commit()

    trace_response = client.get(f"/v1/sessions/{session_id}/runtime-traces/{run_id}")

    assert trace_response.status_code == 200
    payload = trace_response.json()
    assert payload["session_id"] == session_id
    assert payload["run_id"] == run_id
    assert payload["agent_runtime"] == "native_interviewer"
    assert payload["selected_public_runtime"] == "native_interviewer"
    assert payload["runtime_execution"]["execution_runtime"] == "native_interviewer_runtime"
    assert payload["native_run_id"] == run_id
    assert payload["graph_trace"] == {}
    assert payload["graph_events"] == []

    missing_response = client.get(
        f"/v1/sessions/{session_id}/runtime-traces/graph-run-missing"
    )
    assert missing_response.status_code == 404
