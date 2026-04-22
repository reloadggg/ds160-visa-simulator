from __future__ import annotations

from app.domain.contracts import ApplicantProfile, GovernorDecision, RiskFlag, ScoreState
from app.services.governor_service import GovernorService


class BoundaryPolicyService:
    def __init__(self, governor: GovernorService | None = None) -> None:
        self.governor = governor or GovernorService()

    def decide(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
        early_term_candidate: dict | None,
        review_signal: RiskFlag | None = None,
    ) -> dict:
        decision = self.governor.decide(profile, score, early_term_candidate)
        if (
            decision["decision"] != GovernorDecision.SIMULATED_REFUSAL.value
            and review_signal is not None
        ):
            return {
                "decision": GovernorDecision.HIGH_RISK_REVIEW.value,
                "blocked_actions": ["high_risk_review_signal"],
                "rationale_refs": list(review_signal.evidence_refs),
                "requested_documents": list(decision.get("requested_documents", [])),
            }
        return decision
