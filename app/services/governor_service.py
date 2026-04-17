from app.domain.contracts import ApplicantProfile, GovernorDecision, ScoreState


class GovernorService:
    def decide(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
        early_term_candidate: dict | None,
    ) -> dict:
        del profile

        confirmed_terminal_refs = [
            risk.evidence_refs
            for risk in score.risk_flags
            if risk.severity == "high"
            and risk.status == "confirmed"
            and risk.evidence_refs
        ]
        if (
            early_term_candidate
            and early_term_candidate.get("eligible")
            and early_term_candidate.get("evidence_refs")
            and not early_term_candidate.get("confirmation_required", False)
            and confirmed_terminal_refs
        ):
            return {
                "decision": GovernorDecision.SIMULATED_REFUSAL.value,
                "blocked_actions": [],
                "rationale_refs": early_term_candidate["evidence_refs"],
                "requested_documents": [],
            }

        blocked_actions = ["low_score_only_blocked"]
        if early_term_candidate and early_term_candidate.get("eligible"):
            blocked_actions = ["missing_evidence_refs_blocked"]
            if early_term_candidate.get("confirmation_required", False):
                blocked_actions.append("confirmation_required_blocked")

        return {
            "decision": GovernorDecision.NEED_MORE_EVIDENCE.value,
            "blocked_actions": blocked_actions,
            "rationale_refs": [],
            "requested_documents": score.missing_evidence,
        }
