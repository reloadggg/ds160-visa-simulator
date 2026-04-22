from __future__ import annotations

from pydantic import BaseModel, Field

from app.domain.runtime import ScoreHistoryEntry
from app.services.governor_service import DIRECT_REFUSAL_REASON_CODES


class ScoreEvalSummary(BaseModel):
    scoring_stage: str
    risk_level: str
    risk_codes: list[str] = Field(default_factory=list)
    confirmed_high_risk_codes: list[str] = Field(default_factory=list)
    refusal_candidate_codes: list[str] = Field(default_factory=list)
    review_candidate_codes: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    missing_evidence_count: int = 0
    risk_flag_count: int = 0
    document_ready: bool = False
    needs_more_evidence: bool = False


def build_score_eval_summary(
    entry: ScoreHistoryEntry | dict,
) -> ScoreEvalSummary:
    score_entry = (
        entry
        if isinstance(entry, ScoreHistoryEntry)
        else ScoreHistoryEntry.model_validate(entry)
    )
    risk_codes = [item.code for item in score_entry.risk_flags]
    confirmed_high_risk_codes = [
        item.code
        for item in score_entry.risk_flags
        if item.severity == "high" and item.status == "confirmed"
    ]
    review_candidate_codes = [
        item.code for item in score_entry.risk_flags if item.severity == "high"
    ]
    refusal_candidate_codes = [
        code
        for code in confirmed_high_risk_codes
        if code in DIRECT_REFUSAL_REASON_CODES
    ]
    missing_evidence = list(score_entry.missing_evidence)

    return ScoreEvalSummary(
        scoring_stage=score_entry.scoring_stage,
        risk_level=_derive_risk_level(score_entry),
        risk_codes=risk_codes,
        confirmed_high_risk_codes=_unique_list(confirmed_high_risk_codes),
        refusal_candidate_codes=_unique_list(refusal_candidate_codes),
        review_candidate_codes=_unique_list(review_candidate_codes),
        missing_evidence=missing_evidence,
        missing_evidence_count=len(missing_evidence),
        risk_flag_count=len(score_entry.risk_flags),
        document_ready=(
            score_entry.document_readiness >= 60 and not missing_evidence
        ),
        needs_more_evidence=(
            bool(missing_evidence) or score_entry.document_readiness < 60
        ),
    )


def build_score_eval_series(score_history: list[dict] | None) -> list[dict]:
    return [
        build_score_eval_summary(item).model_dump(mode="json")
        for item in list(score_history or [])
    ]


def _derive_risk_level(entry: ScoreHistoryEntry) -> str:
    severities = {item.severity for item in entry.risk_flags}
    if "high" in severities:
        return "high"
    if "medium" in severities or entry.missing_evidence:
        return "medium"
    if entry.risk_flags:
        return "low"
    return "none"


def _unique_list(items: list[str]) -> list[str]:
    deduped: list[str] = []
    for item in items:
        if item not in deduped:
            deduped.append(item)
    return deduped
