from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from app.domain.contracts import FieldState
from app.domain.evidence import DocumentSourceType

ConsistencyFindingType = Literal["gap", "hard_conflict"]
RiskSeverity = Literal["low", "medium", "high"]
FindingStatus = Literal["supported", "confirmed"]
DecisionHint = Literal[
    "continue_interview",
    "need_more_evidence",
    "route_correction",
    "high_risk_review",
    "simulated_refusal",
]


class EvidenceHit(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    evidence_type: str
    field_path: str
    excerpt: str
    filename: str
    source_type: DocumentSourceType
    score: float = Field(ge=0.0)


class EvidenceExcerpt(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    excerpt: str
    filename: str
    source_type: DocumentSourceType


class FieldUpdate(BaseModel):
    field_path: str
    value: str | None = None
    state: FieldState
    evidence_refs: list[str] = Field(default_factory=list)


class ExtractorOutput(BaseModel):
    field_updates: list[FieldUpdate] = Field(default_factory=list)
    required_evidence_queries: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class ConsistencyFinding(BaseModel):
    finding_type: ConsistencyFindingType
    severity: RiskSeverity
    status: FindingStatus
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class RiskFlagProposal(BaseModel):
    code: str
    severity: RiskSeverity
    status: FindingStatus
    summary: str
    evidence_refs: list[str] = Field(default_factory=list)


class ScoreProposal(BaseModel):
    category_fit: int = Field(ge=0, le=100)
    document_readiness: int = Field(ge=0, le=100)
    narrative_consistency: int = Field(ge=0, le=100)
    confidence: int = Field(ge=0, le=100)
    risk_flags: list[RiskFlagProposal] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    requested_documents: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_confirmed_high_risk_evidence(self) -> "ScoreProposal":
        for risk_flag in self.risk_flags:
            if (
                risk_flag.severity == "high"
                and risk_flag.status == "confirmed"
                and not risk_flag.evidence_refs
            ):
                raise ValueError(
                    "confirmed high-risk flags must include evidence_refs"
                )
        return self


class InterviewNextAction(BaseModel):
    assistant_message: str
    requested_documents: list[str] = Field(default_factory=list)
    decision_hint: DecisionHint


class AgentRuntimeDeps(BaseModel):
    session_id: str
    retrieval: object
    evidence: object
