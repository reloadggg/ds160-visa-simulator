from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class FieldState(str, Enum):
    UNKNOWN = "unknown"
    CLAIMED = "claimed"
    DOCUMENTED = "documented"
    CONFIRMED = "confirmed"
    CONFLICTED = "conflicted"


class GovernorDecision(str, Enum):
    CONTINUE_INTERVIEW = "continue_interview"
    NEED_MORE_EVIDENCE = "need_more_evidence"
    ROUTE_CORRECTION = "route_correction"
    HIGH_RISK_REVIEW = "high_risk_review"
    SIMULATED_REFUSAL = "simulated_refusal"


class FieldStateRecord(BaseModel):
    state: FieldState
    last_updated_at: str | None = None


class FieldProvenanceRecord(BaseModel):
    evidence_refs: list[str] = Field(default_factory=list)
    source_summary: str | None = None


class CandidateFamily(BaseModel):
    family: str
    confidence: float = Field(ge=0.0, le=1.0)
    scenario_key: str | None = None


class ApplicantProfile(BaseModel):
    profile_id: str
    profile_version: int = 1
    identity: dict[str, Any] = Field(default_factory=dict)
    visa_intent: dict[str, Any] = Field(default_factory=dict)
    travel: dict[str, Any] = Field(default_factory=dict)
    education: dict[str, Any] = Field(default_factory=dict)
    employment: dict[str, Any] = Field(default_factory=dict)
    funding: dict[str, Any] = Field(default_factory=dict)
    immigration_history: dict[str, Any] = Field(default_factory=dict)
    family_social_ties: dict[str, Any] = Field(default_factory=dict)
    family_specific: dict[str, Any] = Field(default_factory=dict)
    ds160_view: dict[str, Any] = Field(default_factory=dict)
    field_states: dict[str, FieldStateRecord] = Field(default_factory=dict)
    field_provenance: dict[str, FieldProvenanceRecord] = Field(default_factory=dict)

    @classmethod
    def minimal(cls, profile_id: str) -> "ApplicantProfile":
        return cls(
            profile_id=profile_id,
            field_states={
                "/funding/primary_source": FieldStateRecord(
                    state=FieldState.UNKNOWN,
                ),
            },
            field_provenance={
                "/funding/primary_source": FieldProvenanceRecord(),
            },
        )


class RiskFlag(BaseModel):
    code: str
    severity: str
    status: str
    evidence_refs: list[str] = Field(default_factory=list)


class ScoreState(BaseModel):
    score_state_id: str
    profile_version: int
    scoring_stage: str
    category_fit: int = 0
    document_readiness: int = 0
    narrative_consistency: int = 0
    confidence: int = 0
    risk_flags: list[RiskFlag] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)

    @classmethod
    def minimal(cls, profile_version: int, scoring_stage: str) -> "ScoreState":
        return cls(
            score_state_id=f"score-{profile_version}-{scoring_stage}",
            profile_version=profile_version,
            scoring_stage=scoring_stage,
        )
