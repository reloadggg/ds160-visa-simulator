from app.domain.contracts import ApplicantProfile, GovernorDecision, RiskFlag, ScoreState
from app.services.boundary_policy_service import BoundaryPolicyService


def _build_score() -> ScoreState:
    return ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")


def test_boundary_policy_escalates_review_signal_after_governor_continue() -> None:
    score = _build_score()
    decision = BoundaryPolicyService().decide(
        ApplicantProfile.minimal("profile-1"),
        score,
        early_term_candidate=None,
        review_signal=RiskFlag(
            code="record_conflict",
            severity="high",
            status="confirmed",
            evidence_refs=["msg:turn-1"],
        ),
    )

    assert decision == {
        "decision": GovernorDecision.HIGH_RISK_REVIEW.value,
        "blocked_actions": ["high_risk_review_signal"],
        "rationale_refs": ["msg:turn-1"],
        "requested_documents": [],
    }


def test_boundary_policy_keeps_simulated_refusal_when_governor_already_refuses() -> None:
    profile = ApplicantProfile.minimal("profile-2")
    score = _build_score()
    score.risk_flags = [
        RiskFlag(
            code="hard_conflict",
            severity="high",
            status="confirmed",
            evidence_refs=["msg:turn-2"],
        )
    ]

    decision = BoundaryPolicyService().decide(
        profile,
        score,
        early_term_candidate={
            "eligible": True,
            "policy_id": "f1.tp.hard_conflict",
            "reason_code": "hard_conflict",
            "confirmation_required": False,
            "evidence_refs": ["msg:turn-2"],
        },
        review_signal=RiskFlag(
            code="record_conflict",
            severity="high",
            status="confirmed",
            evidence_refs=["msg:turn-3"],
        ),
    )

    assert decision["decision"] == GovernorDecision.SIMULATED_REFUSAL.value
