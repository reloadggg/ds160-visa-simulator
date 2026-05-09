from app.domain.contracts import ApplicantProfile, GovernorDecision, ScoreState

DIRECT_REFUSAL_REASON_CODES = {
    "hard_conflict",
    "fraud_admission",
}


class GovernorService:
    def decide(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
        early_term_candidate: dict | None,
    ) -> dict:
        del profile

        reason_code = self._candidate_reason_code(early_term_candidate)
        confirmed_terminal_refs = self._confirmed_terminal_refs(score, reason_code)
        if (
            early_term_candidate
            and early_term_candidate.get("eligible")
            and early_term_candidate.get("evidence_refs")
            and not early_term_candidate.get("confirmation_required", False)
            and self._is_direct_refusal_reason(reason_code)
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
            blocked_actions = []
            if self._is_direct_refusal_reason(reason_code):
                if not early_term_candidate.get("evidence_refs"):
                    blocked_actions.append("missing_evidence_refs_blocked")
                if early_term_candidate.get("confirmation_required", False):
                    blocked_actions.append("confirmation_required_blocked")
            else:
                blocked_actions.append("non_redline_terminal_blocked")

            if not blocked_actions:
                blocked_actions = ["low_score_only_blocked"]

        if self._requires_more_evidence_for_candidate(early_term_candidate, reason_code):
            return {
                "decision": GovernorDecision.NEED_MORE_EVIDENCE.value,
                "blocked_actions": blocked_actions,
                "rationale_refs": [],
                "requested_documents": [],
            }

        return {
            "decision": GovernorDecision.CONTINUE_INTERVIEW.value,
            "blocked_actions": blocked_actions,
            "rationale_refs": [],
            "requested_documents": [],
        }

    def _candidate_reason_code(
        self,
        early_term_candidate: dict | None,
    ) -> str | None:
        if not early_term_candidate:
            return None

        reason_code = early_term_candidate.get("reason_code")
        if isinstance(reason_code, str) and reason_code:
            return reason_code

        policy_id = early_term_candidate.get("policy_id")
        if not isinstance(policy_id, str) or not policy_id:
            return None

        suffix = policy_id.rsplit(".", 1)[-1]
        return suffix.removeprefix("confirmed_")

    def _confirmed_terminal_refs(
        self,
        score: ScoreState,
        reason_code: str | None,
    ) -> list[list[str]]:
        return [
            risk.evidence_refs
            for risk in score.risk_flags
            if risk.severity == "high"
            and risk.status == "confirmed"
            and risk.evidence_refs
            and (reason_code is None or risk.code == reason_code)
        ]

    def _is_direct_refusal_reason(self, reason_code: str | None) -> bool:
        return isinstance(reason_code, str) and reason_code in DIRECT_REFUSAL_REASON_CODES

    def _requires_more_evidence_for_candidate(
        self,
        early_term_candidate: dict | None,
        reason_code: str | None,
    ) -> bool:
        if not early_term_candidate or not early_term_candidate.get("eligible"):
            return False
        if not early_term_candidate.get("evidence_refs"):
            return True
        if early_term_candidate.get("confirmation_required", False):
            return True
        return (
            self._is_direct_refusal_reason(reason_code)
            and not early_term_candidate.get("evidence_refs")
        )
