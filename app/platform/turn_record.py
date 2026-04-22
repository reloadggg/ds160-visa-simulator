from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class TurnRecord(BaseModel):
    turn_id: str
    session_id: str
    user_turn_id: str | None = None
    assistant_turn_id: str | None = None
    user_input: str
    decision: str
    assistant_message: str
    requested_documents: list[str] = Field(default_factory=list)
    focus: dict[str, Any] = Field(default_factory=dict)
    trace_refs: list[str] = Field(default_factory=list)
    artifacts: list[dict[str, Any]] = Field(default_factory=list)
    advisory_summary: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def create(
        cls,
        *,
        session_id: str,
        user_turn_id: str | None,
        user_input: str,
        decision: str,
        assistant_message: str,
        requested_documents: list[str],
        focus: dict[str, Any] | None,
        trace_refs: list[str],
        artifacts: list[dict[str, Any]] | None = None,
        advisory_summary: dict[str, Any] | None = None,
    ) -> "TurnRecord":
        return cls(
            turn_id=user_turn_id or f"{session_id}:pending-turn",
            session_id=session_id,
            user_turn_id=user_turn_id,
            user_input=user_input,
            decision=decision,
            assistant_message=assistant_message,
            requested_documents=list(requested_documents),
            focus=dict(focus or {}),
            trace_refs=list(trace_refs),
            artifacts=list(artifacts or []),
            advisory_summary=dict(advisory_summary or {}),
        )

    def with_assistant_turn(self, assistant_turn_id: str) -> "TurnRecord":
        return self.model_copy(
            update={
                "turn_id": assistant_turn_id,
                "assistant_turn_id": assistant_turn_id,
            }
        )
