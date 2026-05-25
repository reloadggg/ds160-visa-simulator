from __future__ import annotations

from time import time_ns
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.settings import settings
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
from app.services.graph_adjudication_node import GraphAdjudicationNode
from app.services.graph_case_state_builder import GraphCaseStateBuilder
from app.services.graph_knowledge_plane_service import GraphKnowledgePlaneService
from app.services.graph_response_mapper import GraphResponseMapper


class GraphRuntimeAdapter:
    """唯一 graph runtime 入口；当前阶段只做 deterministic shadow run。"""

    def __init__(
        self,
        db: Session,
        *,
        case_state_builder: GraphCaseStateBuilder | None = None,
        response_mapper: GraphResponseMapper | None = None,
        adjudication_node: GraphAdjudicationNode | None = None,
        knowledge_plane: GraphKnowledgePlaneService | None = None,
    ) -> None:
        self.db = db
        self.session_turn_repo = SessionTurnRepository(db)
        self.document_repo = DocumentRepository(db)
        self.evidence_repo = EvidenceRepository(db)
        self.case_state_builder = case_state_builder or GraphCaseStateBuilder()
        self.response_mapper = response_mapper or GraphResponseMapper()
        self.adjudication_node = adjudication_node or GraphAdjudicationNode()
        self.knowledge_plane = knowledge_plane or GraphKnowledgePlaneService()

    def run_turn(
        self,
        record: SessionRecord,
        message_text: str,
        *,
        user_turn: Any | None = None,
    ) -> dict[str, Any]:
        return self._run_graph(
            record,
            message_text=message_text,
            trigger="user_turn",
            user_turn=user_turn,
        )

    def run_material_change(
        self,
        record: SessionRecord,
        *,
        reason: str,
    ) -> dict[str, Any]:
        return self._run_graph(
            record,
            message_text=reason,
            trigger="material_change",
            material_change_reason=reason,
            user_turn=None,
        )

    def _run_graph(
        self,
        record: SessionRecord,
        *,
        message_text: str,
        trigger: str,
        material_change_reason: str | None = None,
        user_turn: Any | None = None,
    ) -> dict[str, Any]:
        run_id = self._build_run_id()
        user_turn_id = self._string_or_none(getattr(user_turn, "turn_id", None))
        graph = DeterministicDS160TurnGraph(
            nodes={
                "receive_turn": self._receive_turn_node(
                    message_text=message_text,
                    trigger=trigger,
                    material_change_reason=material_change_reason,
                    user_turn_id=user_turn_id,
                ),
                "build_case_state": self._build_case_state_node(record),
                "plan_retrieval": self._plan_retrieval_node(message_text),
                "build_citation_bundle": self._build_citation_bundle_node(),
                "adjudicate": self._adjudication_node(record, message_text),
                "deterministic_grounding_guard": fake_guard_node(),
            }
        )
        state, events = graph.run(
            session_id=record.session_id,
            run_id=run_id,
            client_turn_id=user_turn_id,
            message_text=message_text,
            trigger=trigger,
            material_change_reason=material_change_reason,
        )
        payload = self.response_mapper.to_message_response(state, events)
        payload["graph_runtime_engine"] = "langgraph"
        payload["graph_runtime_engine_class"] = graph.graph_runtime_name
        payload["graph_events"] = [
            event.model_dump(mode="json") for event in events
        ]
        return payload

    def _plan_retrieval_node(self, message_text: str):
        def _node(state: DS160GraphState) -> DS160GraphState:
            retrieval_plan = self.knowledge_plane.build_retrieval_plan(
                state.case_state,
                message_text=message_text,
            )
            return state.model_copy(update={"retrieval_plan": retrieval_plan})

        return _node

    def _build_citation_bundle_node(self):
        def _node(state: DS160GraphState) -> DS160GraphState:
            citation_bundle, summary = self.knowledge_plane.build_citation_bundle(
                state.case_state,
                retrieval_plan=state.retrieval_plan,
                run_id=state.run_id,
            )
            material_review = {
                **(state.material_review or {}),
                "knowledge_plane": summary,
            }
            return state.model_copy(
                update={
                    "citation_bundle": citation_bundle,
                    "material_review": material_review,
                }
            )

        return _node

    def _adjudication_node(
        self,
        record: SessionRecord,
        message_text: str,
    ):
        if not settings.agent_runtime_typed_adjudication_enabled:
            return fake_adjudication_node(
                assistant_message="我会继续围绕你的 DS-160 材料做下一步核对。",
                decision="continue_interview",
            )

        def _node(state: DS160GraphState) -> DS160GraphState:
            result = self.adjudication_node.run(
                state,
                message_text=message_text,
                declared_family=record.declared_family,
            )
            return result.state.model_copy(
                update={
                    "adjudication_result": {
                        **(result.state.adjudication_result or {}),
                        "metadata": result.metadata,
                    }
                }
            )

        return _node

    def _receive_turn_node(
        self,
        *,
        message_text: str,
        trigger: str,
        material_change_reason: str | None,
        user_turn_id: str | None,
    ):
        def _node(state: DS160GraphState) -> DS160GraphState:
            if trigger == "material_change":
                return state.model_copy(
                    update={
                        "user_turn": {
                            "turn_id": None,
                            "content": message_text,
                            "source": "material_change",
                            "reason": material_change_reason,
                        }
                    }
                )
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
