from app.platform.runtime_ledger import RuntimeViewState
from app.services.runtime_view_contract_service import RuntimeViewContractService


def test_anchored_runtime_view_requested_documents_override_fallback() -> None:
    runtime_view_state = {
        "source_turn_id": "turn-assistant-1",
        "requested_documents": ["relationship_proof_between_applicant_and_sponsors"],
        "remaining_required_documents": [
            "relationship_proof_between_applicant_and_sponsors"
        ],
    }
    fallback = {
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
    }

    assert RuntimeViewContractService.requested_documents(
        runtime_view_state,
        fallback,
    ) == ["relationship_proof_between_applicant_and_sponsors"]
    assert RuntimeViewContractService.remaining_required_documents(
        runtime_view_state,
        fallback,
    ) == ["relationship_proof_between_applicant_and_sponsors"]


def test_anchored_runtime_view_empty_requested_documents_do_not_fallback() -> None:
    runtime_view_state = {
        "source_turn_id": "turn-assistant-1",
        "requested_documents": [],
        "remaining_required_documents": [],
    }
    fallback = {
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
    }

    assert (
        RuntimeViewContractService.requested_documents(runtime_view_state, fallback)
        == []
    )
    assert (
        RuntimeViewContractService.remaining_required_documents(
            runtime_view_state,
            fallback,
        )
        == []
    )


def test_non_anchored_fallback_empty_requested_documents_stays_authoritative() -> None:
    runtime_view_state = {
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
    }
    fallback = {
        "requested_documents": [],
        "remaining_required_documents": [],
    }

    assert (
        RuntimeViewContractService.requested_documents(runtime_view_state, fallback)
        == []
    )
    assert (
        RuntimeViewContractService.remaining_required_documents(
            runtime_view_state,
            fallback,
        )
        == []
    )


def test_non_anchored_runtime_view_is_used_when_fallback_field_is_missing() -> None:
    runtime_view_state = {
        "requested_documents": ["funding_proof"],
        "remaining_required_documents": ["funding_proof"],
    }

    assert RuntimeViewContractService.requested_documents(runtime_view_state, {}) == [
        "funding_proof"
    ]
    assert RuntimeViewContractService.remaining_required_documents(
        runtime_view_state,
        {},
    ) == ["funding_proof"]


def test_runtime_view_state_payload_keeps_explicit_contract_defaults() -> None:
    runtime_view_state = RuntimeViewState(
        source_turn_id="turn-assistant-1",
        source_turn_content="What will you study?",
        decision="continue_interview",
        governor_decision="continue_interview",
        public_status="continue_interview",
        current_focus={
            "kind": "interview_question",
            "question": "What will you study?",
        },
        current_key_question="What will you study?",
        current_key_proof=None,
        requested_documents=[],
        remaining_required_documents=["funding_proof"],
        advisory_context={"missing_evidence": ["funding_proof"]},
    )

    payload = RuntimeViewContractService.payload(runtime_view_state)

    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == ["funding_proof"]
    assert payload["advisory_context"]["missing_evidence"] == ["funding_proof"]
    assert payload["current_key_proof"] is None
    assert payload["current_focus"]["kind"] == "interview_question"


def test_turn_decision_keeps_requested_and_remaining_documents_separate() -> None:
    runtime_view_state = {
        "source_turn_id": "turn-assistant-1",
        "decision": "continue_interview",
        "current_key_question": "What will you study?",
        "current_key_proof": None,
        "requested_documents": [],
        "remaining_required_documents": ["funding_proof"],
    }
    fallback = {
        "turn_decision": {
            "decision": "need_more_evidence",
            "requested_documents": ["funding_proof"],
            "remaining_required_documents": ["funding_proof"],
            "current_key_proof": "funding_proof",
        }
    }

    payload = RuntimeViewContractService.turn_decision(runtime_view_state, fallback)

    assert payload["decision"] == "continue_interview"
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == ["funding_proof"]
    assert payload["current_key_proof"] is None
