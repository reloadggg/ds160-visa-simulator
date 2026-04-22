from __future__ import annotations

from app.domain.contracts import InterviewRiskLevel, ScoreState
from app.domain.runtime import TurnAdvisoryContext


class AdvisoryReviewService:
    def build_context(self, score: ScoreState) -> TurnAdvisoryContext:
        missing_evidence = list(score.missing_evidence)
        return TurnAdvisoryContext(
            score_summary={
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            risk_codes=self.extract_risk_codes(score),
            missing_evidence=missing_evidence,
            risk_level=self.derive_risk_level(score),
            missing_evidence_summary=(
                ", ".join(missing_evidence) if missing_evidence else None
            ),
        )

    def extract_risk_codes(self, score: ScoreState) -> list[str]:
        return [item.code for item in score.risk_flags]

    def derive_risk_level(self, score: ScoreState) -> InterviewRiskLevel:
        severities = {item.severity for item in score.risk_flags}
        if "high" in severities:
            return InterviewRiskLevel.HIGH
        if "medium" in severities:
            return InterviewRiskLevel.MEDIUM
        if "low" in severities:
            return InterviewRiskLevel.LOW
        return InterviewRiskLevel.NONE
