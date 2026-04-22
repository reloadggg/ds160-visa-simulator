from app.domain.contracts import InterviewRiskLevel, RiskFlag, ScoreState
from app.services.advisory_review_service import AdvisoryReviewService


def _build_score() -> ScoreState:
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.category_fit = 61
    score.document_readiness = 42
    score.narrative_consistency = 77
    score.confidence = 68
    return score


def test_advisory_review_builds_context_from_score_state() -> None:
    score = _build_score()
    score.missing_evidence = ["funding_proof"]
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        )
    ]

    context = AdvisoryReviewService().build_context(score)

    assert context.score_summary == {
        "category_fit": 61,
        "document_readiness": 42,
        "narrative_consistency": 77,
        "confidence": 68,
    }
    assert context.risk_codes == ["supporting_evidence_missing"]
    assert context.missing_evidence == ["funding_proof"]
    assert context.risk_level == InterviewRiskLevel.MEDIUM
    assert context.missing_evidence_summary == "funding_proof"
