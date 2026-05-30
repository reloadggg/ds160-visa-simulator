from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


MaterialUnderstandingStatus = Literal["queued", "processing", "completed", "failed"]
MaterialUnderstandingTrigger = Literal["upload", "debug_bundle", "reprocess"]
EvidenceSourceType = Literal[
    "uploaded_file",
    "user_turn",
    "debug_material",
    "policy",
]
CaseClaimStatus = Literal["stated", "documented", "contradicted", "unknown"]
ProofPointStatus = Literal[
    "supported",
    "partial",
    "missing",
    "contradicted",
    "not_applicable",
]
InterviewMoveType = Literal[
    "ask",
    "clarify_conflict",
    "probe_risk",
    "simulate_refusal",
    "summarize",
]
EvidenceRelation = Literal["support", "conflict", "unknown"]


class DocumentTypeCandidate(BaseModel):
    document_type: str
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("document_type")
    @classmethod
    def validate_document_type(cls, value: str) -> str:
        return _non_empty(value, "document_type")


class EvidenceCard(BaseModel):
    evidence_id: str
    source_type: EvidenceSourceType
    document_id: str | None = None
    page_number: int | None = Field(default=None, ge=1)
    excerpt: str
    visual_anchor: str | None = None
    claim_refs: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("evidence_id", "excerpt")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "evidence card text")

    @field_validator("document_id", "visual_anchor")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("claim_refs")
    @classmethod
    def normalize_claim_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)


class ClaimEvidenceLink(BaseModel):
    claim_id: str
    evidence_id: str
    relation: EvidenceRelation = "support"

    @field_validator("claim_id", "evidence_id")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "claim/evidence id")


class CaseClaim(BaseModel):
    claim_id: str
    field_path: str
    value: str | None = None
    status: CaseClaimStatus = "unknown"
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    conflicting_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("claim_id", "field_path")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "case claim text")

    @field_validator("value")
    @classmethod
    def normalize_value(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @field_validator("supporting_evidence_ids", "conflicting_evidence_ids")
    @classmethod
    def normalize_evidence_ids(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @model_validator(mode="after")
    def validate_status_has_matching_evidence(self) -> "CaseClaim":
        if self.status == "documented" and not self.supporting_evidence_ids:
            raise ValueError("documented claims require supporting evidence")
        if self.status == "contradicted" and not self.conflicting_evidence_ids:
            raise ValueError("contradicted claims require conflicting evidence")
        return self


class ProofPoint(BaseModel):
    proof_point_id: str
    visa_family: str
    question: str
    status: ProofPointStatus = "missing"
    why_it_matters: str
    claim_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "proof_point_id",
        "visa_family",
        "question",
        "why_it_matters",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "proof point text")

    @field_validator("claim_refs", "evidence_refs")
    @classmethod
    def normalize_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)


class CaseConflict(BaseModel):
    conflict_id: str
    claim_ids: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    summary: str
    severity: Literal["low", "medium", "high"] = "medium"
    suggested_followup: str | None = None

    @field_validator("conflict_id", "summary")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "case conflict text")

    @field_validator("claim_ids", "evidence_ids")
    @classmethod
    def normalize_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @field_validator("suggested_followup")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)


class CaseConflictResolution(BaseModel):
    conflict_id: str
    status: Literal["resolved"] = "resolved"
    note: str | None = None

    @field_validator("conflict_id")
    @classmethod
    def validate_conflict_id(cls, value: str) -> str:
        return _non_empty(value, "case conflict resolution id")

    @field_validator("note")
    @classmethod
    def normalize_note(cls, value: str | None) -> str | None:
        return _optional_text(value)


class InterviewNextMove(BaseModel):
    move_type: InterviewMoveType
    question: str
    reason: str
    claim_refs: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)

    @field_validator("question", "reason")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "interview next move text")

    @field_validator("claim_refs", "evidence_refs")
    @classmethod
    def normalize_refs(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)


class MaterialUnderstandingResult(BaseModel):
    document_type_candidates: list[DocumentTypeCandidate] = Field(default_factory=list)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    extracted_claims: list[CaseClaim] = Field(default_factory=list)
    proof_points: list[ProofPoint] = Field(default_factory=list)
    conflicts: list[CaseConflict] = Field(default_factory=list)
    unknowns: list[str] = Field(default_factory=list)
    suggested_followups: list[InterviewNextMove] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    @field_validator("unknowns")
    @classmethod
    def normalize_unknowns(cls, value: list[str]) -> list[str]:
        return _dedupe_texts(value)

    @model_validator(mode="after")
    def validate_referenced_evidence_exists(self) -> "MaterialUnderstandingResult":
        evidence_ids = {item.evidence_id for item in self.evidence_cards}
        for claim in self.extracted_claims:
            referenced = set(claim.supporting_evidence_ids) | set(
                claim.conflicting_evidence_ids
            )
            missing = referenced - evidence_ids
            if missing:
                raise ValueError(f"claim references unknown evidence ids: {sorted(missing)}")
        return self


class MaterialUnderstandingJob(BaseModel):
    job_id: str
    document_id: str
    status: MaterialUnderstandingStatus = "queued"
    trigger: MaterialUnderstandingTrigger = "upload"
    result: MaterialUnderstandingResult | None = None
    error_code: str | None = None
    error_message: str | None = None

    @field_validator("job_id", "document_id")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        return _non_empty(value, "material understanding job text")

    @field_validator("error_code", "error_message")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return _optional_text(value)

    @model_validator(mode="after")
    def validate_terminal_payload(self) -> "MaterialUnderstandingJob":
        if self.status == "completed" and self.result is None:
            raise ValueError("completed material understanding jobs require result")
        if self.status == "failed" and not (self.error_code or self.error_message):
            raise ValueError("failed material understanding jobs require error details")
        return self


class CaseMemorySnapshot(BaseModel):
    latest_material: dict[str, Any] | None = None
    claims: list[CaseClaim] = Field(default_factory=list)
    evidence_cards: list[EvidenceCard] = Field(default_factory=list)
    proof_points: list[ProofPoint] = Field(default_factory=list)
    conflicts: list[CaseConflict] = Field(default_factory=list)
    conflict_resolutions: list[CaseConflictResolution] = Field(default_factory=list)
    next_move: InterviewNextMove | None = None

    @model_validator(mode="after")
    def validate_unique_ids(self) -> "CaseMemorySnapshot":
        _validate_unique("claim ids", [item.claim_id for item in self.claims])
        _validate_unique(
            "evidence ids", [item.evidence_id for item in self.evidence_cards]
        )
        _validate_unique(
            "proof point ids", [item.proof_point_id for item in self.proof_points]
        )
        _validate_unique("conflict ids", [item.conflict_id for item in self.conflicts])
        _validate_unique(
            "conflict resolution ids",
            [item.conflict_id for item in self.conflict_resolutions],
        )
        return self


def _non_empty(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be empty")
    return normalized


def _optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _dedupe_texts(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for value in values:
        item = value.strip()
        if item and item not in normalized:
            normalized.append(item)
    return normalized


def _validate_unique(label: str, values: list[str]) -> None:
    if len(values) != len(set(values)):
        raise ValueError(f"{label} must be unique")
