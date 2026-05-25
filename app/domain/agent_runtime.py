from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator


GraphSchemaVersion = Literal["agent-runtime.v1"]

KnowledgeSourceType = Literal[
    "official_policy",
    "case_evidence",
    "product_rubric",
]
SourceAuthority = Literal[
    "official",
    "embassy",
    "institutional",
    "user_provided",
    "product",
    "third_party_reference",
]
StalenessPolicy = Literal[
    "stable",
    "expires",
    "refresh_required",
    "invalidated",
]
PublicClaimType = Literal[
    "official_policy",
    "case_evidence",
    "product_guidance",
    "conversation_state",
]
AssistantMessageAuthor = Literal[
    "adjudication_agent",
    "deterministic_safe_fallback",
]
GuardStatus = Literal[
    "passed",
    "failed",
    "fallback_required",
]
GuardViolationCode = Literal[
    "missing_policy_citation",
    "missing_case_evidence",
    "internal_field_leak",
    "repeated_template",
    "schema_invalid",
    "unsupported_refusal",
    "provider_error",
    "retrieval_error",
    "checkpoint_error",
    "guard_retry_exhausted",
]
NextSafeAction = Literal[
    "continue_interview",
    "ask_clarification",
    "request_document",
    "retry_later",
    "manual_review",
    "end_session",
]
GraphEventType = Literal[
    "accepted",
    "state_built",
    "retrieval_started",
    "retrieval_completed",
    "material_review_completed",
    "adjudication_completed",
    "guard_completed",
    "retrying",
    "fallback_used",
    "final",
    "error",
]
GraphTrigger = Literal["user_turn", "material_change"]


class RetryBudget(BaseModel):
    max_llm_calls: int = Field(default=1, ge=0)
    max_adjudication_retries: int = Field(default=1, ge=0)
    llm_calls_used: int = Field(default=0, ge=0)
    adjudication_retries_used: int = Field(default=0, ge=0)

    @property
    def can_call_llm(self) -> bool:
        return self.llm_calls_used < self.max_llm_calls

    @property
    def can_retry_adjudication(self) -> bool:
        return self.adjudication_retries_used < self.max_adjudication_retries

    def consume_llm_call(self) -> "RetryBudget":
        if not self.can_call_llm:
            raise ValueError("LLM call retry budget exhausted")
        return self.model_copy(update={"llm_calls_used": self.llm_calls_used + 1})

    def consume_adjudication_retry(self) -> "RetryBudget":
        if not self.can_retry_adjudication:
            raise ValueError("adjudication retry budget exhausted")
        return self.model_copy(
            update={
                "adjudication_retries_used": self.adjudication_retries_used + 1
            }
        )


class CitationRef(BaseModel):
    citation_id: str
    source_type: KnowledgeSourceType
    source_authority: SourceAuthority
    source_id: str
    document_id: str
    chunk_id: str
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    content_hash: str
    quote_or_summary: str
    retrieved_at: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    published_or_effective_date: date | None = None
    staleness_policy: StalenessPolicy = "stable"
    claim_ids: list[str] = Field(default_factory=list)

    @field_validator(
        "citation_id",
        "source_id",
        "document_id",
        "chunk_id",
        "content_hash",
        "quote_or_summary",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("citation text fields must not be empty")
        return normalized

    @field_validator("claim_ids")
    @classmethod
    def normalize_claim_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            claim_id = item.strip()
            if claim_id and claim_id not in normalized:
                normalized.append(claim_id)
        return normalized

    @model_validator(mode="after")
    def validate_span(self) -> "CitationRef":
        if self.span_end <= self.span_start:
            raise ValueError("citation span_end must be greater than span_start")
        return self


class CitationBundle(BaseModel):
    citations: list[CitationRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_unique_citation_ids(self) -> "CitationBundle":
        citation_ids = [item.citation_id for item in self.citations]
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("citation ids must be unique")
        return self

    @property
    def citation_ids(self) -> set[str]:
        return {item.citation_id for item in self.citations}


class PublicClaim(BaseModel):
    claim_id: str
    claim_type: PublicClaimType
    text: str
    citation_ids: list[str] = Field(default_factory=list)

    @field_validator("claim_id", "text")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("public claim text fields must not be empty")
        return normalized

    @field_validator("citation_ids")
    @classmethod
    def normalize_citation_ids(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            citation_id = item.strip()
            if citation_id and citation_id not in normalized:
                normalized.append(citation_id)
        return normalized

    @model_validator(mode="after")
    def validate_required_citations(self) -> "PublicClaim":
        if self.claim_type in {"official_policy", "case_evidence"} and not self.citation_ids:
            raise ValueError(f"{self.claim_type} claims require citation ids")
        return self


class GroundingViolation(BaseModel):
    code: GuardViolationCode
    detail: str
    claim_id: str | None = None

    @field_validator("detail")
    @classmethod
    def validate_detail(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("guard violation detail must not be empty")
        return normalized


class GroundingCheckResult(BaseModel):
    status: GuardStatus
    violations: list[GroundingViolation] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_failure_has_violations(self) -> "GroundingCheckResult":
        if self.status in {"failed", "fallback_required"} and not self.violations:
            raise ValueError("failed guard results require at least one violation")
        return self


class GraphRunResult(BaseModel):
    assistant_message: str
    assistant_message_author: AssistantMessageAuthor = "adjudication_agent"
    decision: str
    requested_documents: list[str] = Field(default_factory=list)
    public_claims: list[PublicClaim] = Field(default_factory=list)
    used_citation_ids: list[str] = Field(default_factory=list)
    guard_status: GuardStatus = "passed"
    incomplete_reason: GuardViolationCode | None = None
    next_safe_action: NextSafeAction = "continue_interview"

    @field_validator("assistant_message", "decision")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("graph run result text fields must not be empty")
        return normalized

    @field_validator("requested_documents", "used_citation_ids")
    @classmethod
    def normalize_string_lists(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            candidate = item.strip()
            if candidate and candidate not in normalized:
                normalized.append(candidate)
        return normalized

    @model_validator(mode="after")
    def validate_claim_citations_are_used(self) -> "GraphRunResult":
        used = set(self.used_citation_ids)
        for claim in self.public_claims:
            missing = set(claim.citation_ids) - used
            if missing:
                raise ValueError(
                    f"public claim references unused citation ids: {sorted(missing)}"
                )
        if self.guard_status in {"failed", "fallback_required"} and self.incomplete_reason is None:
            raise ValueError("failed or fallback guard status requires incomplete_reason")
        return self


class GraphEvent(BaseModel):
    event_type: GraphEventType
    run_id: str
    sequence: int = Field(ge=0)
    schema_version: GraphSchemaVersion = "agent-runtime.v1"
    payload: dict[str, Any] = Field(default_factory=dict)

    @field_validator("run_id")
    @classmethod
    def validate_run_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("run_id must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_final_payload(self) -> "GraphEvent":
        if self.event_type == "final" and not self.payload.get("final_response"):
            raise ValueError("final graph events require final_response payload")
        if self.event_type == "error" and "error_code" not in self.payload:
            raise ValueError("error graph events require error_code payload")
        return self


class DS160GraphState(BaseModel):
    schema_version: GraphSchemaVersion = "agent-runtime.v1"
    session_id: str
    run_id: str
    trigger: GraphTrigger = "user_turn"
    material_change_reason: str | None = None
    client_turn_id: str | None = None
    user_turn: dict[str, Any] = Field(default_factory=dict)
    case_state: dict[str, Any] = Field(default_factory=dict)
    retrieval_plan: dict[str, Any] = Field(default_factory=dict)
    citation_bundle: CitationBundle = Field(default_factory=CitationBundle)
    material_review: dict[str, Any] | None = None
    adjudication_result: dict[str, Any] | None = None
    guard_result: GroundingCheckResult | None = None
    final_response: GraphRunResult | None = None
    node_timings: dict[str, float] = Field(default_factory=dict)
    retry_budget: RetryBudget = Field(default_factory=RetryBudget)

    @field_validator("session_id", "run_id")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("graph state ids must not be empty")
        return normalized

    @field_validator("material_change_reason")
    @classmethod
    def normalize_material_change_reason(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_material_change_trigger(self) -> "DS160GraphState":
        if self.trigger == "material_change" and self.material_change_reason is None:
            raise ValueError("material_change trigger requires material_change_reason")
        return self

    @model_validator(mode="after")
    def validate_final_response_citations(self) -> "DS160GraphState":
        if self.final_response is None:
            return self
        unknown = set(self.final_response.used_citation_ids) - self.citation_bundle.citation_ids
        if unknown:
            raise ValueError(
                f"final response references unknown citation ids: {sorted(unknown)}"
            )
        return self
