from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentSourceType(str, Enum):
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"
    UNKNOWN = "unknown"


class DocumentArtifact(BaseModel):
    document_id: str
    session_id: str
    filename: str
    source_type: DocumentSourceType
    parser_name: str
    status: str
    page_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentAssessmentMainFlowFeedback(BaseModel):
    status: str
    supported_document_type: str | None = None
    current_focus_document_type: str | None = None
    message: str


class DocumentAssessment(BaseModel):
    document_type: str | None = None
    document_type_hint: str | None = None
    document_type_candidates: list[str] = Field(default_factory=list)
    relevance: str | None = None
    supported_claims: list[str] = Field(default_factory=list)
    confidence: float | None = None
    feedback_message: str | None = None
    relevant: bool | None = None
    counts_toward_gate: bool | None = None
    main_flow_feedback: DocumentAssessmentMainFlowFeedback | None = None

    @classmethod
    def from_artifact(cls, artifact_json: dict[str, Any] | None) -> "DocumentAssessment":
        artifact = dict(artifact_json or {})
        metadata = dict(artifact.get("metadata", {}) or {})
        nested_assessment = artifact.get("document_assessment")
        metadata_assessment = metadata.get("document_assessment")
        base_payload: dict[str, Any] = {}
        if isinstance(metadata_assessment, dict):
            base_payload.update(metadata_assessment)
        if isinstance(nested_assessment, dict):
            base_payload.update(nested_assessment)

        payload: dict[str, Any] = {
            "document_type": _first_non_none(
                base_payload.get("document_type"),
                artifact.get("document_type"),
                metadata.get("document_type"),
            ),
            "document_type_hint": _first_non_none(
                base_payload.get("document_type_hint"),
                artifact.get("document_type_hint"),
                metadata.get("document_type_hint"),
            ),
            "document_type_candidates": _normalize_string_list(
                _first_non_none(
                    base_payload.get("document_type_candidates"),
                    artifact.get("document_type_candidates"),
                    metadata.get("document_type_candidates"),
                    [],
                )
            ),
            "relevance": _first_non_none(
                base_payload.get("relevance"),
                artifact.get("relevance"),
                metadata.get("relevance"),
            ),
            "supported_claims": _normalize_string_list(
                _first_non_none(
                    base_payload.get("supported_claims"),
                    artifact.get("supported_claims"),
                    metadata.get("supported_claims"),
                    [],
                )
            ),
            "confidence": _first_non_none(
                base_payload.get("confidence"),
                artifact.get("confidence"),
                metadata.get("confidence"),
            ),
            "feedback_message": _first_non_none(
                base_payload.get("feedback_message"),
                artifact.get("feedback_message"),
                metadata.get("feedback_message"),
            ),
            "relevant": _first_non_none(
                base_payload.get("relevant"),
                artifact.get("relevant"),
                metadata.get("relevant"),
            ),
            "counts_toward_gate": _first_non_none(
                base_payload.get("counts_toward_gate"),
                artifact.get("counts_toward_gate"),
                metadata.get("counts_toward_gate"),
            ),
            "main_flow_feedback": _first_non_none(
                base_payload.get("main_flow_feedback"),
                artifact.get("main_flow_feedback"),
                metadata.get("main_flow_feedback"),
            ),
        }

        main_flow_feedback = payload.get("main_flow_feedback")
        if isinstance(main_flow_feedback, dict):
            payload["main_flow_feedback"] = (
                DocumentAssessmentMainFlowFeedback.model_validate(main_flow_feedback)
            )
        elif main_flow_feedback is None:
            payload.pop("main_flow_feedback", None)
        else:
            payload["main_flow_feedback"] = (
                DocumentAssessmentMainFlowFeedback.model_validate(
                    main_flow_feedback.model_dump(mode="json")
                )
            )

        return cls.model_validate(payload)

    def to_metadata_payload(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    session_id: str
    ordinal: int
    page_number: int | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    excerpt: str


class EvidenceItem(BaseModel):
    evidence_id: str
    session_id: str
    document_id: str
    chunk_id: str
    evidence_type: str
    field_path: str
    value: str | None = None
    excerpt: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            excerpt=self.excerpt,
        )


def _first_non_none(*values: Any) -> Any:
    for value in values:
        if value is not None:
            return value
    return None


def _normalize_string_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    return [str(value) for value in values if isinstance(value, str) and value]
