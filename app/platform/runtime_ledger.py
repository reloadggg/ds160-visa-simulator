from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class LedgerEventType(str, Enum):
    TRACE = "trace"
    CAPABILITY = "capability"
    SCORER = "scorer"
    BOUNDARY = "boundary"
    ADVISORY = "advisory"


class LedgerEvent(BaseModel):
    event_id: str
    session_id: str
    turn_id: str | None = None
    turn_index: int | None = None
    event_type: LedgerEventType
    source: str
    name: str
    payload: dict[str, Any] = Field(default_factory=dict)


class TurnLedger(BaseModel):
    turn_id: str
    turn_index: int
    session_id: str
    role: str
    content: str
    source: str
    metadata: dict[str, Any] = Field(default_factory=dict)
    turn_record: dict[str, Any] | None = None
    event_ids: list[str] = Field(default_factory=list)


class SessionLedger(BaseModel):
    session_id: str
    phase_state: str
    declared_family: str | None = None
    current_governor_decision: str | None = None
    current_focus: dict[str, Any] = Field(default_factory=dict)
    interviewer_state: dict[str, Any] = Field(default_factory=dict)
    turns: list[TurnLedger] = Field(default_factory=list)
    events: list[LedgerEvent] = Field(default_factory=list)


class RuntimeViewState(BaseModel):
    source_turn_id: str | None = None
    source_turn_content: str | None = None
    decision: str
    governor_decision: str
    public_status: str | None = None
    risk_level: str | None = None
    current_focus: dict[str, Any] = Field(default_factory=dict)
    current_key_question: str | None = None
    current_key_proof: str | None = None
    current_risk_code: str | None = None
    requested_documents: list[str] = Field(default_factory=list)
    remaining_required_documents: list[str] = Field(default_factory=list)
    allowed_next_actions: list[str] = Field(default_factory=list)
    advisory_context: dict[str, Any] = Field(default_factory=dict)
    document_review: dict[str, Any] = Field(default_factory=dict)
    prompt_trace: dict[str, Any] = Field(default_factory=dict)


class SessionReadModel(BaseModel):
    session_id: str
    phase_state: str
    declared_family: str | None = None
    current_governor_decision: str | None = None
    runtime_ledger: SessionLedger
    runtime_view_state: RuntimeViewState
