from __future__ import annotations

from time import time_ns
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.evidence_models import DocumentChunkRecord
from app.db.models import SessionRecord
from app.domain.agent_runtime import DS160GraphState
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.agent_runtime_graph import (
    DeterministicDS160TurnGraph,
    fake_adjudication_node,
    fake_guard_node,
)
from app.services.graph_case_state_builder import GraphCaseStateBuilder
from app.services.graph_response_mapper import GraphResponseMapper


class GraphRuntimeAdapter:
    """唯一 graph runtime 入口；当前阶段只做 deterministic shadow run。"""

    def __init__(
        self,
        db: Session,
        *,
        case_state_builder: GraphCaseStateBuilder | None = None,
        response_mapper: GraphResponseMapper | None = None,
    ) -> None:
        self.db = db
        self.session_turn_repo = SessionTurnRepository(db)
        self.document_repo = DocumentRepository(db)
        self.evidence_repo = EvidenceRepository(db)
        self.case_state_builder = case_state_builder or GraphCaseStateBuilder()
        self.response_mapper = response_mapper or GraphResponseMapper()

    def run_turn(
        self,
        record: SessionRecord,
        message_text: str,
        *,
        user_turn: Any | None = None,
    ) -> dict[str, Any]:
        run_id = self._build_run_id()
        user_turn_id = self._string_or_none(getattr(user_turn, "turn_id", None))
        graph = DeterministicDS160TurnGraph(
            nodes={
                "receive_turn": self._receive_turn_node(
                    message_text=message_text,
                    user_turn_id=user_turn_id,
                ),
                "build_case_state": self._build_case_state_node(record),
                "adjudicate": fake_adjudication_node(
                    assistant_message="我会继续围绕你的 DS-160 材料做下一步核对。",
                    decision="continue_interview",
                ),
                "deterministic_grounding_guard": fake_guard_node(),
            }
        )
        state, events = graph.run(
            session_id=record.session_id,
            run_id=run_id,
            client_turn_id=user_turn_id,
            message_text=message_text,
        )
        payload = self.response_mapper.to_message_response(state, events)
        payload["graph_events"] = [
            event.model_dump(mode="json") for event in events
        ]
        return payload

    def _receive_turn_node(
        self,
        *,
        message_text: str,
        user_turn_id: str | None,
    ):
        def _node(state: DS160GraphState) -> DS160GraphState:
            return state.model_copy(
                update={
                    "user_turn": {
                        "turn_id": user_turn_id,
                        "content": message_text,
                        "source": "user_message",
                    }
                }
            )

        return _node

    def _build_case_state_node(self, record: SessionRecord):
        def _node(state: DS160GraphState) -> DS160GraphState:
            case_state = self.case_state_builder.build(
                record,
                self.session_turn_repo.list_session_turns(record.session_id),
                documents=self.document_repo.list_session_documents(record.session_id),
                evidence_items=self.evidence_repo.list_session_evidence(
                    record.session_id
                ),
                document_chunks=self._list_session_document_chunks(record.session_id),
            )
            return state.model_copy(update={"case_state": case_state})

        return _node

    def _list_session_document_chunks(
        self,
        session_id: str,
    ) -> list[DocumentChunkRecord]:
        statement = (
            select(DocumentChunkRecord)
            .where(DocumentChunkRecord.session_id == session_id)
            .order_by(
                DocumentChunkRecord.document_id,
                DocumentChunkRecord.ordinal,
                DocumentChunkRecord.chunk_id,
            )
        )
        return list(self.db.scalars(statement))

    def _build_run_id(self) -> str:
        return f"graph-run-{time_ns():020d}-{uuid4().hex[:8]}"

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
