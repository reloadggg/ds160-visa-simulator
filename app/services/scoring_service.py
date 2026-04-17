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
        self.client.generate_json(
            module_key="scoring_engine",
            stage_key=scoring_stage,
            payload={"finding_count": len(findings)},
        )
        score = ScoreState.minimal(
            profile_version=profile.profile_version,
            scoring_stage=scoring_stage,
        )
        score.category_fit = 60 if profile.visa_intent.get("declared_family") else 30
        score.document_readiness = 70
        score.narrative_consistency = 75
        score.confidence = 65
        for finding in findings:
            if finding["finding_type"] == "gap":
                score.document_readiness = 40
                score.narrative_consistency = 55
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
