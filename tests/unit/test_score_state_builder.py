from app.agents.schemas import ScoreProposal
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    RiskFlag,
)
from app.services.score_state_builder import ScoreStateBuilder


def test_score_state_builder_from_proposal_normalizes_missing_evidence_aliases() -> None:
    profile = ApplicantProfile.minimal("profile-builder-1")
    profile.visa_intent["declared_family"] = "f1"

    score = ScoreStateBuilder().from_proposal(
        profile,
        ScoreProposal.model_validate(
            {
                "category_fit": 74,
                "document_readiness": 66,
                "narrative_consistency": 71,
                "confidence": 69,
                "risk_flags": [],
                "missing_evidence": ["bank_statement", "travel_history"],
                "requested_documents": ["funding_proof"],
            }
        ),
        "interview_turn",
        findings=[],
    )

    assert score.missing_evidence == ["funding_proof", "travel_history"]


def test_score_state_builder_fallback_applies_gap_guard() -> None:
    profile = ApplicantProfile.minimal("profile-builder-2")
    profile.visa_intent["declared_family"] = "f1"
    profile.funding["primary_source"] = "parents"

    score = ScoreStateBuilder().build_fallback(
        profile,
        findings=[
            {
                "finding_type": "gap",
                "severity": "medium",
                "status": "supported",
                "summary": "funding source claimed but not yet documented",
                "evidence_refs": [],
            }
        ],
        scoring_stage="interview_turn",
    )

    assert score.document_readiness == 40
    assert score.narrative_consistency == 55
    assert score.missing_evidence == ["funding_proof"]
    assert [item.code for item in score.risk_flags] == ["supporting_evidence_missing"]


def test_score_state_builder_reconcile_profile_evidence_clears_funding_gap() -> None:
    profile = ApplicantProfile.minimal("profile-builder-3")
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord(
        evidence_refs=["evi-1"],
        source_summary="bank statement",
    )

    score_builder = ScoreStateBuilder()
    score = score_builder.build_fallback(
        profile,
        findings=[],
        scoring_stage="interview_turn",
    )
    score.missing_evidence = ["funding_proof", "travel_history"]
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        ),
        RiskFlag(
            code="travel_history_gap",
            severity="low",
            status="supported",
            evidence_refs=[],
        ),
    ]

    reconciled = score_builder.reconcile_profile_evidence(profile, score)

    assert reconciled.missing_evidence == ["travel_history"]
    assert [item.code for item in reconciled.risk_flags] == ["travel_history_gap"]
