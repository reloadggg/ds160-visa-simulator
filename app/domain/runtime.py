from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
