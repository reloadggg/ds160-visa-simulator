from types import SimpleNamespace

from app.services.message_service import MessageService


def test_material_refresh_response_empty_documents_override_stale_interviewer_state() -> None:
    service = MessageService.__new__(MessageService)
    record = SimpleNamespace(
        current_governor_decision="need_more_evidence",
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        interviewer_state_json={
            "governor_decision": "need_more_evidence",
            "decision": "need_more_evidence",
            "public_status": "need_more_evidence",
            "risk_level": "none",
            "current_key_question": None,
            "current_key_proof": "funding_proof",
            "current_risk_code": None,
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
            "allowed_next_actions": ["request_document"],
        },
    )
    response = {
        "assistant_message": "",
        "governor_decision": "continue_interview",
        "requested_documents": [],
        "remaining_required_documents": [],
        "turn_decision": {"decision": "continue_interview"},
        "document_review": {},
        "prompt_trace": {},
        "agent_runtime": "graph",
        "selected_public_runtime": "native_interviewer",
        "runtime_execution": {},
    }

    service._sync_material_refresh_response_state(
        record,
        response,
        reason="debug_fill:funding_proof",
    )

    assert response["requested_documents"] == []
    assert response["remaining_required_documents"] == []
    assert response["runtime_view_state"]["requested_documents"] == []
    assert response["runtime_view_state"]["remaining_required_documents"] == []
    assert (
        record.interviewer_state_json["last_material_refresh"]["runtime_view_state"][
            "requested_documents"
        ]
        == []
    )
    assert (
        record.interviewer_state_json["last_material_refresh"]["runtime_view_state"][
            "remaining_required_documents"
        ]
        == []
    )


def test_material_refresh_missing_documents_falls_back_to_interviewer_state() -> None:
    service = MessageService.__new__(MessageService)
    record = SimpleNamespace(
        current_governor_decision="need_more_evidence",
        current_focus_json={
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "funding_proof",
        },
        interviewer_state_json={
            "governor_decision": "need_more_evidence",
            "decision": "need_more_evidence",
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
        },
    )
    response = {
        "assistant_message": "",
        "governor_decision": "need_more_evidence",
        "turn_decision": {"decision": "need_more_evidence"},
    }

    service._sync_material_refresh_response_state(
        record,
        response,
        reason="materials_updated",
    )

    assert response["requested_documents"] == ["funding_proof"]
    assert response["remaining_required_documents"] == ["funding_proof"]


def test_gate_response_turn_record_keeps_explicit_empty_remaining_documents() -> None:
    service = MessageService.__new__(MessageService)
    record = SimpleNamespace(
        session_id="sess-test",
        current_governor_decision=None,
        current_focus_json={},
    )
    response = {
        "assistant_message": "请先选择签证家族。",
        "governor_decision": "need_more_evidence",
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": [],
    }

    service._apply_gate_response_state(
        record,
        response,
        user_input="hello",
        user_turn_id="turn-user-1",
    )

    assert response["turn_record"]["requested_documents"] == ["funding_proof"]
    assert response["turn_record"]["remaining_required_documents"] == []
