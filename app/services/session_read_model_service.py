from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db.models import SessionRecord
from app.platform.runtime_ledger import SessionReadModel
from app.repositories.session_repo import SessionRepository
from app.services.runtime_ledger_service import RuntimeLedgerService


class SessionReadModelService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.runtime_ledger = RuntimeLedgerService(db)

    def build(self, session_id: str) -> SessionReadModel:
        record = self.session_repo.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")
        return self.build_from_record(record)

    def build_from_record(
        self,
        record: SessionRecord,
        *,
        turns: list[Any] | None = None,
    ) -> SessionReadModel:
        runtime_ledger = self.runtime_ledger.build_from_record(record, turns=turns)
        runtime_view_state = self.runtime_ledger.latest_view_state(
            runtime_ledger,
            fallback_governor_decision=record.current_governor_decision,
        )
        return SessionReadModel(
            session_id=record.session_id,
            phase_state=record.phase_state,
            declared_family=record.declared_family,
            current_governor_decision=record.current_governor_decision,
            runtime_ledger=runtime_ledger,
            runtime_view_state=runtime_view_state,
        )
