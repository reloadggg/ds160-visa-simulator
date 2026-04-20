from __future__ import annotations

import re
from typing import Literal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator

from app.domain.contracts import FieldState
from app.domain.evidence import DocumentSourceType

ConsistencyFindingType = Literal[
    "gap",
    "hard_conflict",
    "record_conflict",
    "evasive_answer",
    "unresolved_key_proof_gap",
]
RiskSeverity = Literal["low", "medium", "high"]
FindingStatus = Literal["supported", "confirmed"]
DecisionHint = Literal[
    "continue_interview",
    "need_more_evidence",
    "route_correction",
    "high_risk_review",
    "simulated_refusal",
]
FocusKind = Literal[
    "interview_question",
    "required_document",
    "route_correction",
    "risk_review",
    "refusal",
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
    model_config = ConfigDict(populate_by_name=True)

    decision: DecisionHint = Field(
        validation_alias=AliasChoices("decision", "decision_hint")
    )
    assistant_message: str
    requested_documents: list[str] = Field(default_factory=list)
    focus_kind: FocusKind | None = None
    focus_document_type: str | None = None
    focus_risk_code: str | None = None
    reason: str | None = None

    @field_validator("assistant_message")
    @classmethod
    def validate_assistant_message(cls, value: str) -> str:
        message = value.strip()
        if not message:
            raise ValueError("assistant_message must not be empty")
        return message

    @field_validator("requested_documents")
    @classmethod
    def validate_requested_documents(cls, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            document_type = item.strip()
            if not document_type:
                continue
            if document_type not in normalized:
                normalized.append(document_type)
        if len(normalized) > 1:
            raise ValueError("requested_documents must contain at most one document")
        return normalized

    @model_validator(mode="after")
    def validate_single_focus_output(self) -> "InterviewNextAction":
        if self._looks_like_summary_or_checklist(self.assistant_message):
            raise ValueError(
                "assistant_message must stay focused on one point without summary or checklist output"
            )
        if self.requested_documents and not self._looks_like_material_request(
            self.assistant_message,
            self.requested_documents,
        ):
            raise ValueError(
                "assistant_message must align with the single requested document focus"
            )
        if self.requested_documents and self.decision != "need_more_evidence":
            raise ValueError(
                "requested_documents may only be set when decision is need_more_evidence"
            )
        if self.focus_kind is None:
            self.focus_kind = self._default_focus_kind(self.decision)
        if self.focus_kind == "required_document":
            if self.focus_document_type is None and self.requested_documents:
                self.focus_document_type = self.requested_documents[0]
        if self.focus_kind == "risk_review" and not self.focus_risk_code:
            self.focus_risk_code = None
        return self

    @property
    def decision_hint(self) -> DecisionHint:
        return self.decision

    @staticmethod
    def _default_focus_kind(decision: DecisionHint) -> FocusKind:
        mapping: dict[DecisionHint, FocusKind] = {
            "continue_interview": "interview_question",
            "need_more_evidence": "required_document",
            "route_correction": "route_correction",
            "high_risk_review": "risk_review",
            "simulated_refusal": "refusal",
        }
        return mapping[decision]

    @staticmethod
    def _looks_like_summary_or_checklist(message: str) -> bool:
        lines = [line.strip() for line in message.splitlines() if line.strip()]
        bullet_pattern = re.compile(
            r"^(?:[-*•]|\d+[.)]|[一二三四五六七八九十]+[、.)])\s*"
        )
        if "\n\n" in message:
            return True
        if len(lines) >= 3:
            return True
        if sum(bool(bullet_pattern.match(line)) for line in lines) >= 2:
            return True
        normalized = " ".join(lines).lower()
        obvious_markers = (
            "总结如下",
            "材料清单",
            "请提供以下",
            "以下材料",
            "需要准备",
            "please provide the following",
            "the following documents",
            "summary:",
            "in summary",
            "checklist",
        )
        if any(marker in normalized for marker in obvious_markers):
            return True
        return bool(re.search(r"\b1[.)]\s+.+\b2[.)]\s+", normalized))

    @staticmethod
    def _looks_like_material_request(
        message: str,
        requested_documents: list[str],
    ) -> bool:
        normalized = message.lower()
        request_markers = (
            "upload",
            "provide",
            "submit",
            "send",
            "document",
            "documents",
            "proof",
            "evidence",
            "材料",
            "证明",
            "补充",
            "上传",
            "提供",
            "提交",
        )
        if any(marker in normalized for marker in request_markers):
            return True

        for document_type in requested_documents:
            aliases = {
                document_type.lower(),
                document_type.lower().replace("_", " "),
                document_type.lower().replace("_", ""),
            }
            if any(alias and alias in normalized for alias in aliases):
                return True
        return False


class AgentRuntimeDeps(BaseModel):
    session_id: str
    retrieval: object
    evidence: object
