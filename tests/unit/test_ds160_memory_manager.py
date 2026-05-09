from types import SimpleNamespace

from app.domain.contracts import ApplicantProfile, ScoreState
from app.domain.runtime import TurnAdvisoryContext
from app.platform.runtime_ledger import RuntimeViewState, SessionLedger, SessionReadModel
from app.services.ds160_memory_manager import DS160MemoryManager


def test_memory_manager_counts_candidate_document_type_as_verified() -> None:
    profile = ApplicantProfile.minimal("profile-sess-1")
    score = ScoreState.minimal(profile_version=1, scoring_stage="interview_turn")
    read_model = SessionReadModel(
        session_id="sess-1",
        declared_family="f1",
        phase_state="interview",
        current_governor_decision="need_more_evidence",
        runtime_ledger=SessionLedger(
            session_id="sess-1",
            phase_state="interview",
            declared_family="f1",
        ),
        runtime_view_state=RuntimeViewState(
            decision="need_more_evidence",
            governor_decision="need_more_evidence",
            current_focus={
                "kind": "required_document",
                "document_type": "relationship_proof_between_applicant_and_sponsors",
            },
            requested_documents=["relationship_proof_between_applicant_and_sponsors"],
            remaining_required_documents=[
                "relationship_proof_between_applicant_and_sponsors"
            ],
        ),
    )
    document = SimpleNamespace(
        document_id="doc-1",
        filename="hukou.jpg",
        status="parsed",
        artifact_json={
            "metadata": {
                "document_assessment": {
                    "document_type": "funding_proof",
                    "document_type_candidates": [
                        "funding_proof",
                        "relationship_proof_between_applicant_and_sponsors",
                    ],
                    "supported_claims": ["/family/parent_names"],
                    "counts_toward_gate": True,
                }
            }
        },
    )

    bundle = DS160MemoryManager().build(
        profile=profile,
        score=score,
        advisory_context=TurnAdvisoryContext(),
        read_model=read_model,
        declared_family="f1",
        phase_state="interview",
        boundary_decision="need_more_evidence",
        documents=[document],
    )

    assert bundle.evidence_digest.verified_documents == [
        "relationship_proof_between_applicant_and_sponsors"
    ]
    assert bundle.evidence_digest.remaining_required_documents == []
