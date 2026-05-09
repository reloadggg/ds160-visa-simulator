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
