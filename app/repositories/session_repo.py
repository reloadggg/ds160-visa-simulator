from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import SessionRecord
from app.domain.runtime import (
    GovernorHistoryEntry,
    RuntimeTraceEntry,
    ScoreHistoryEntry,
    empty_governor_history,
    empty_runtime_trace,
    empty_score_history,
)


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(
        self,
        declared_family: str | None,
        gate_status_json: dict,
    ) -> SessionRecord:
        record = SessionRecord(
            session_id=f"sess-{uuid4().hex[:12]}",
            declared_family=declared_family,
            gate_status_json=gate_status_json,
            runtime_trace_json=empty_runtime_trace(),
            score_history_json=empty_score_history(),
            governor_history_json=empty_governor_history(),
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self.db.get(SessionRecord, session_id)

    def append_runtime_history(
        self,
        record: SessionRecord,
        *,
        runtime_trace: list[RuntimeTraceEntry] | None = None,
        score_history: list[ScoreHistoryEntry] | None = None,
        governor_history: list[GovernorHistoryEntry] | None = None,
    ) -> SessionRecord:
        if runtime_trace:
            record.runtime_trace_json = [
                *(record.runtime_trace_json or []),
                *(item.model_dump(mode="json") for item in runtime_trace),
            ]
        if score_history:
            record.score_history_json = [
                *(record.score_history_json or []),
                *(item.model_dump(mode="json") for item in score_history),
            ]
        if governor_history:
            record.governor_history_json = [
                *(record.governor_history_json or []),
                *(item.model_dump(mode="json") for item in governor_history),
            ]
        return record

    def save(self, record: SessionRecord) -> SessionRecord:
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record
