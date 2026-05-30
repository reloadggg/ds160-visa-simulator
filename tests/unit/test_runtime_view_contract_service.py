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
