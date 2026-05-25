from time import time_ns
from uuid import uuid4

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import SessionTurnRecord

MAX_APPEND_RETRIES = 3


class SessionTurnRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def append_user_turn(
        self,
        *,
        session_id: str,
        content: str,
        source: str,
        metadata_json: dict | None = None,
        commit: bool = True,
    ) -> SessionTurnRecord:
        return self._append_turn(
            session_id=session_id,
            role="user",
            content=content,
            source=source,
            metadata_json=metadata_json,
            commit=commit,
        )

    def append_assistant_turn(
        self,
        *,
        session_id: str,
        content: str,
        source: str,
        metadata_json: dict | None = None,
        commit: bool = True,
    ) -> SessionTurnRecord:
        return self._append_turn(
            session_id=session_id,
            role="assistant",
            content=content,
            source=source,
            metadata_json=metadata_json,
            commit=commit,
        )

    def list_session_turns(self, session_id: str) -> list[SessionTurnRecord]:
        statement = (
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index, SessionTurnRecord.turn_id)
        )
        return list(self.db.scalars(statement))

    def _append_turn(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        source: str,
        metadata_json: dict | None,
        commit: bool,
    ) -> SessionTurnRecord:
        if not commit:
            return self._append_turn_without_commit(
                session_id=session_id,
                role=role,
                content=content,
                source=source,
                metadata_json=metadata_json,
            )

        attempted_turn_index: int | None = None
        for _ in range(MAX_APPEND_RETRIES):
            record = self._build_turn_record(
                session_id=session_id,
                role=role,
                content=content,
                source=source,
                metadata_json=metadata_json,
                minimum_turn_index=(
                    attempted_turn_index + 1
                    if attempted_turn_index is not None
                    else None
                ),
            )
            attempted_turn_index = record.turn_index
            self.db.add(record)
            try:
                self.db.commit()
            except IntegrityError:
                self.db.rollback()
                continue
            self.db.refresh(record)
            return record

        raise RuntimeError("failed to append session turn with a stable turn_index")

    def _append_turn_without_commit(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        source: str,
        metadata_json: dict | None,
    ) -> SessionTurnRecord:
        attempted_turn_index: int | None = None
        for _ in range(MAX_APPEND_RETRIES):
            try:
                with self.db.begin_nested():
                    record = self._build_turn_record(
                        session_id=session_id,
                        role=role,
                        content=content,
                        source=source,
                        metadata_json=metadata_json,
                        minimum_turn_index=(
                            attempted_turn_index + 1
                            if attempted_turn_index is not None
                            else None
                        ),
                    )
                    attempted_turn_index = record.turn_index
                    self.db.add(record)
                    self.db.flush()
                return record
            except IntegrityError:
                continue

        raise RuntimeError("failed to append session turn with a stable turn_index")

    def _build_turn_record(
        self,
        *,
        session_id: str,
        role: str,
        content: str,
        source: str,
        metadata_json: dict | None,
        minimum_turn_index: int | None = None,
    ) -> SessionTurnRecord:
        turn_index = self._next_turn_index(session_id)
        if minimum_turn_index is not None:
            turn_index = max(turn_index, minimum_turn_index)
        return SessionTurnRecord(
            turn_id=self._build_turn_id(),
            turn_index=turn_index,
            session_id=session_id,
            role=role,
            content=content,
            source=source,
            metadata_json=metadata_json or {},
        )

    def _build_turn_id(self) -> str:
        return f"turn-{time_ns():020d}-{uuid4().hex[:8]}"

    def _next_turn_index(self, session_id: str) -> int:
        statement = select(func.max(SessionTurnRecord.turn_index)).where(
            SessionTurnRecord.session_id == session_id
        )
        current_max = self.db.scalar(statement)
        return (current_max or 0) + 1
