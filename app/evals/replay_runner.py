from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionRecord, SessionTurnRecord
from app.evals.advisory_scorers import build_score_eval_series
from app.platform.runtime_ledger import SessionLedger, TurnLedger
from app.services.runtime_ledger_service import RuntimeLedgerService


class ReplayRunner:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.runtime_ledger = RuntimeLedgerService(db)

    def inspect_turn(self, session_id: str, turn_id: str) -> dict:
        ledger = self.runtime_ledger.build_session_ledger(session_id)
        turn = next((item for item in ledger.turns if item.turn_id == turn_id), None)
        if turn is None:
            raise LookupError(f"Turn not found: {session_id}/{turn_id}")
        return self._serialize_turn(turn, ledger)

    def replay_session(self, session_id: str) -> dict:
        session_record = self.db.get(SessionRecord, session_id)
        if session_record is None:
            raise LookupError(f"Session not found: {session_id}")
        turns = self.db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index, SessionTurnRecord.turn_id)
        ).all()
        ledger = self.runtime_ledger.build_from_record(session_record, turns=turns)
        return {
            "session_id": session_id,
            "phase_state": ledger.phase_state,
            "turn_count": len(ledger.turns),
            "score_evals": build_score_eval_series(
                self.runtime_ledger.scorer_payloads(ledger)
            ),
            "turns": [self._serialize_turn(turn, ledger) for turn in ledger.turns],
        }

    def _serialize_turn(
        self,
        turn: SessionTurnRecord | TurnLedger,
        ledger: SessionLedger,
    ) -> dict:
        metadata = self._turn_metadata(turn)
        payload = {
            "turn_id": turn.turn_id,
            "turn_index": turn.turn_index,
            "session_id": turn.session_id,
            "role": turn.role,
            "content": turn.content,
            "source": turn.source,
            "metadata": metadata,
            "events": self.runtime_ledger.events_for_turn(ledger, turn.turn_id),
        }
        turn_record = metadata.get("turn_record")
        if isinstance(turn_record, dict):
            payload["turn_record"] = turn_record
        return payload

    def _turn_metadata(self, turn: SessionTurnRecord | TurnLedger) -> dict:
        if isinstance(turn, TurnLedger):
            return dict(turn.metadata)
        return dict(turn.metadata_json or {})
