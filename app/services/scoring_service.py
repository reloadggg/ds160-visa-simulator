from typing import Any

from app.domain.contracts import ApplicantProfile, RiskFlag, ScoreState
from app.integrations.llm_client import LLMClient


class ScoringService:
    def __init__(self) -> None:
        self.client = LLMClient()

    def propose(
        self,
        profile: ApplicantProfile,
        findings: list[dict],
        scoring_stage: str,
    ) -> ScoreState:
        runtime_payload = self.client.generate_json(
            module_key="scoring_engine",
            stage_key=scoring_stage,
            payload={
                "declared_family": profile.visa_intent.get("declared_family"),
                "funding": profile.funding,
                "field_states": {
                    field_path: field_record.state.value
                    for field_path, field_record in profile.field_states.items()
                },
                "findings": findings,
            },
        )
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )

        default_scores = self._default_scores(profile)
        score.category_fit = default_scores["category_fit"]
        score.document_readiness = default_scores["document_readiness"]
        score.narrative_consistency = default_scores["narrative_consistency"]
        score.confidence = default_scores["confidence"]

        response_json = runtime_payload.get("response_json")
        if isinstance(response_json, dict):
            score.category_fit = self._bounded_score(
                response_json.get("category_fit"),
                default_scores["category_fit"],
            )
            score.document_readiness = self._bounded_score(
                response_json.get("document_readiness"),
                default_scores["document_readiness"],
            )
            score.narrative_consistency = self._bounded_score(
                response_json.get("narrative_consistency"),
                default_scores["narrative_consistency"],
            )
            score.confidence = self._bounded_score(
                response_json.get("confidence"),
                default_scores["confidence"],
            )
            for item in response_json.get("missing_evidence_suggestions", []):
                if isinstance(item, str) and item not in score.missing_evidence:
                    score.missing_evidence.append(item)

        for finding in findings:
            if finding["finding_type"] == "gap":
                score.document_readiness = min(score.document_readiness, 40)
                score.narrative_consistency = min(score.narrative_consistency, 55)
                score.risk_flags.append(
                    RiskFlag(
                        code="supporting_evidence_missing",
                        severity="medium",
                        status="supported",
                        evidence_refs=[],
                    )
                )
                if "funding_proof" not in score.missing_evidence:
                    score.missing_evidence.append("funding_proof")
                continue

            score.document_readiness = min(score.document_readiness, 30)
            score.narrative_consistency = min(score.narrative_consistency, 15)
            score.confidence = max(score.confidence, 85)
            score.risk_flags.append(
                RiskFlag(
                    code=finding["finding_type"],
                    severity=finding["severity"],
                    status=finding.get("status", "supported"),
                    evidence_refs=finding.get("evidence_refs", []),
                )
            )
        return score

    def _default_scores(self, profile: ApplicantProfile) -> dict[str, int]:
        return {
            "category_fit": 60 if profile.visa_intent.get("declared_family") else 30,
            "document_readiness": 70,
            "narrative_consistency": 75,
            "confidence": 65,
        }

    def _bounded_score(self, value: Any, fallback: int) -> int:
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return fallback
        return max(0, min(100, parsed))
