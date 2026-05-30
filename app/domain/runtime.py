from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.contracts import InterviewRiskLevel, InterviewStateStatus


class GateOverallStatus:
    FAMILY_NOT_SELECTED = "family_not_selected"
    PENDING_DOCUMENTS = "pending_documents"
    WAITING_FOR_PARSE = "waiting_for_parse"
    READY_FOR_INTERVIEW = "ready_for_interview"


class RequiredDocumentRuntimeStatus(BaseModel):
    document_type: str
    status: str = "missing"
    is_uploaded: bool = False
    is_parsed: bool = False
    meets_minimum_fields: bool = False


class SessionGateStatus(BaseModel):
    declared_family: str | None = None
    scenario_key: str | None = None
    status: str
    required_documents: list[RequiredDocumentRuntimeStatus] = Field(default_factory=list)

    @classmethod
    def initial(
        cls,
        declared_family: str | None,
        required_documents: list[str],
        scenario_key: str | None = None,
    ) -> "SessionGateStatus":
        if declared_family is None:
            return cls(
                declared_family=None,
                scenario_key=None,
                status=GateOverallStatus.FAMILY_NOT_SELECTED,
            )

        return cls(
            declared_family=declared_family,
            scenario_key=scenario_key,
            status=GateOverallStatus.PENDING_DOCUMENTS,
            required_documents=[
                RequiredDocumentRuntimeStatus(document_type=document_type)
                for document_type in required_documents
            ],
        )


class RuntimeTraceEntry(BaseModel):
    node_name: str
    summary: str | None = None
    prompt_pack_id: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None
    tool_calls: list[dict[str, Any]] = Field(default_factory=list)
    turn_decision: str | None = None
    fallback_used: bool = False
    retry_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class PromptTrace(BaseModel):
    prompt_pack_id: str | None = None
    prompt_version: str | None = None
    provider: str | None = None
    model: str | None = None
    reasoning_effort: str | None = None


class PromptRoleContract(BaseModel):
    system: str = "stable_policy"
    dynamic_turn_context: str = "dynamic_turn_context"
    tool_outputs: str = "tool_outputs"
    user: str = "user"


class TurnAdvisoryContext(BaseModel):
    score_summary: dict[str, int] = Field(default_factory=dict)
    risk_codes: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    risk_level: InterviewRiskLevel = InterviewRiskLevel.NONE
    missing_evidence_summary: str | None = None


class DS160CaseBrief(BaseModel):
    declared_family: str | None = None
    phase_state: str | None = None
    boundary_decision: str | None = None
    last_turn_decision: str | None = None
    profile_version: int | None = None
    travel_purpose: str | None = None
    school_name: str | None = None
    funding_source: str | None = None


class DS160FocusThread(BaseModel):
    current_focus: dict[str, Any] = Field(default_factory=dict)
    last_turn_decision: str | None = None
    public_status: str | None = None
    current_key_question: str | None = None
    current_key_proof: str | None = None
    current_risk_code: str | None = None
    requested_documents: list[str] = Field(default_factory=list)
    allowed_next_actions: list[str] = Field(default_factory=list)


class DS160EvidenceDigest(BaseModel):
    missing_evidence: list[str] = Field(default_factory=list)
    requested_documents: list[str] = Field(default_factory=list)
    current_focus_document_type: str | None = None
    documented_field_paths: list[str] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    supported_claims: list[str] = Field(default_factory=list)
    active_main_flow_feedback: dict[str, Any] = Field(default_factory=dict)
    uploaded_document_count: int = 0
    uploaded_documents: list[dict[str, Any]] = Field(default_factory=list)
    remaining_required_documents: list[str] = Field(default_factory=list)
    verified_documents: list[str] = Field(default_factory=list)


class DS160MemoryStrata(BaseModel):
    facts_memory: dict[str, Any] = Field(default_factory=dict)
    working_memory: dict[str, Any] = Field(default_factory=dict)
    evidence_memory: dict[str, Any] = Field(default_factory=dict)
    derived_memory: dict[str, Any] = Field(default_factory=dict)
    audit_memory: dict[str, Any] = Field(default_factory=dict)


class TurnHistorySummary(BaseModel):
    summarized_turn_count: int = 0
    summarized_user_turn_count: int = 0
    summarized_assistant_turn_count: int = 0
    prior_decisions: list[str] = Field(default_factory=list)
    prior_requested_documents: list[str] = Field(default_factory=list)
    prior_question_topics: list[str] = Field(default_factory=list)


class ContextCompressionSnapshot(BaseModel):
    strategy: str = "recent_turns_tail+history_summary"
    recent_turn_window: int = 6
    retained_turn_count: int = 0
    summarized_turn_count: int = 0


class DS160MemoryBundle(BaseModel):
    case_brief: DS160CaseBrief = Field(default_factory=DS160CaseBrief)
    focus_thread: DS160FocusThread = Field(default_factory=DS160FocusThread)
    evidence_digest: DS160EvidenceDigest = Field(default_factory=DS160EvidenceDigest)
    case_board: dict[str, Any] = Field(default_factory=dict)
    evidence_graph: dict[str, Any] = Field(default_factory=dict)
    memory_strata: DS160MemoryStrata = Field(default_factory=DS160MemoryStrata)
    current_focus: dict[str, Any] = Field(default_factory=dict)
    last_turn_decision: str | None = None


class TurnContextSnapshot(BaseModel):
    session_id: str
    declared_family: str | None = None
    phase_state: str
    latest_user_message: str
    recent_turns: list[dict[str, str]] = Field(default_factory=list)
    profile_snapshot: dict[str, Any] = Field(default_factory=dict)
    current_focus: dict[str, Any] = Field(default_factory=dict)
    advisory_context: TurnAdvisoryContext = Field(default_factory=TurnAdvisoryContext)
    gate_progress: dict[str, Any] = Field(default_factory=dict)
    last_turn_decision: str | None = None
    prompt_roles: PromptRoleContract = Field(default_factory=PromptRoleContract)
    case_brief: DS160CaseBrief = Field(default_factory=DS160CaseBrief)
    focus_thread: DS160FocusThread = Field(default_factory=DS160FocusThread)
    evidence_digest: DS160EvidenceDigest = Field(default_factory=DS160EvidenceDigest)
    case_board: dict[str, Any] = Field(default_factory=dict)
    evidence_graph: dict[str, Any] = Field(default_factory=dict)
    memory_strata: DS160MemoryStrata = Field(default_factory=DS160MemoryStrata)
    capability_plan: list[dict[str, Any]] = Field(default_factory=list)
    history_summary: TurnHistorySummary = Field(default_factory=TurnHistorySummary)
    compression: ContextCompressionSnapshot = Field(default_factory=ContextCompressionSnapshot)


class RiskFlagHistoryEntry(BaseModel):
    code: str
    severity: str
    status: str
    evidence_refs: list[str] = Field(default_factory=list)


class ScoreHistoryEntry(BaseModel):
    scoring_stage: str
    category_fit: int
    document_readiness: int
    narrative_consistency: int
    confidence: int
    missing_evidence: list[str] = Field(default_factory=list)
    risk_flags: list[RiskFlagHistoryEntry] = Field(default_factory=list)
    summary: str | None = None


class GovernorHistoryEntry(BaseModel):
    decision: str
    summary: str | None = None


class InterviewAllowedNextAction(str, Enum):
    ANSWER_QUESTION = "answer_question"
    CONTINUE_INTERVIEW = "continue_interview"
    CLARIFY_KEY_ISSUE = "clarify_key_issue"
    UPLOAD_KEY_PROOF = "upload_key_proof"
    EXPLAIN_MISSING_PROOF = "explain_missing_proof"
    WAIT_FOR_REVIEW = "wait_for_review"
    REVIEW_REFUSAL_RESULT = "review_refusal_result"


class InterviewStateSnapshot(BaseModel):
    owner: str = "interviewer_runtime_service"
    status: InterviewStateStatus
    public_status: InterviewStateStatus
    decision: str
    governor_decision: str
    next_action: str
    decision_hint: str
    current_key_question: str | None = None
    current_key_proof: str | None = None
    current_risk_code: str | None = None
    risk_level: InterviewRiskLevel = InterviewRiskLevel.NONE
    allowed_next_actions: list[InterviewAllowedNextAction] = Field(default_factory=list)
    requested_documents: list[str] = Field(default_factory=list)
    remaining_required_documents: list[str] = Field(default_factory=list)
    risk_codes: list[str] = Field(default_factory=list)
    history_turn_count: int = 0
    document_review: dict[str, Any] = Field(default_factory=dict)


def build_initial_gate_status(
    declared_family: str | None,
    required_documents: list[str],
    scenario_key: str | None = None,
) -> dict[str, Any]:
    return SessionGateStatus.initial(
        declared_family=declared_family,
        scenario_key=scenario_key,
        required_documents=required_documents,
    ).model_dump(mode="json")


def empty_runtime_trace() -> list[dict[str, Any]]:
    return []


def empty_score_history() -> list[dict[str, Any]]:
    return []


def empty_governor_history() -> list[dict[str, Any]]:
    return []
