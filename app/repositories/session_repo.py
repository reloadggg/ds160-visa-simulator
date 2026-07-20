from uuid import uuid4

from sqlalchemy import select
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
        *,
        commit: bool = True,
    ) -> SessionRecord:
        record = SessionRecord(
            session_id=f"sess-{uuid4().hex[:12]}",
            declared_family=declared_family,
            gate_status_json=gate_status_json,
            runtime_trace_json=empty_runtime_trace(),
            score_history_json=empty_score_history(),
            governor_history_json=empty_governor_history(),
            interviewer_state_json={},
            current_focus_json={},
        )
        self.db.add(record)
        if commit:
            self.db.commit()
            self.db.refresh(record)
        else:
            self.db.flush()
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self.db.get(SessionRecord, session_id)

    def get_for_update(self, session_id: str) -> SessionRecord | None:
        """Load session row with ``SELECT ... FOR UPDATE``.

        On PostgreSQL this takes a row lock until the current transaction ends.
        On SQLite the FOR UPDATE clause is effectively a no-op (SQLite only
        locks the whole DB file on write, and the lock does not span unlocked
        multi-second work such as LLM calls). Callers that need cross-request
        mutual exclusion under SQLite must also use a committed processing
        flag or similar application-level guard (see MessageService).
        """
        statement = (
            select(SessionRecord)
            .where(SessionRecord.session_id == session_id)
            .with_for_update()
        )
        return self.db.scalars(statement).first()

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
