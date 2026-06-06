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
from app.domain.contracts import GovernorDecision
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.agent_runtime_graph import (
    DeterministicDS160TurnGraph,
    fake_adjudication_node,
    fake_guard_node,
)
from app.services.case_memory_service import CaseMemoryService
from app.services.graph_adjudication_node import GraphAdjudicationNode
from app.services.interview_case_state_builder import InterviewCaseStateBuilder
from app.services.graph_knowledge_plane_service import GraphKnowledgePlaneService
from app.services.graph_response_mapper import GraphResponseMapper


class GraphRuntimeAdapter:
    """Experimental graph compatibility harness, not the public source of truth."""

    def __init__(
        self,
        db: Session,
        *,
        case_state_builder: InterviewCaseStateBuilder | None = None,
        response_mapper: GraphResponseMapper | None = None,
        adjudication_node: GraphAdjudicationNode | None = None,
        knowledge_plane: GraphKnowledgePlaneService | None = None,
    ) -> None:
        self.db = db
        self.session_turn_repo = SessionTurnRepository(db)
        self.document_repo = DocumentRepository(db)
        self.evidence_repo = EvidenceRepository(db)
        self.case_memory = CaseMemoryService(db)
        self.case_state_builder = case_state_builder or InterviewCaseStateBuilder()
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
        payload["runtime_execution"] = {
            "schema_version": "runtime.execution.v1",
            "configured_runtime": "graph",
            "requested_public_runtime": "experimental_graph",
            "public_runtime": "experimental_graph",
            "execution_runtime": "graph_runtime_adapter",
            "runtime_engine": "langgraph",
            "runtime_engine_class": graph.graph_runtime_name,
            "source": trigger,
            "runtime_role": "experimental",
            "canonical": False,
        }
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
            return self._case_memory_fallback_adjudication_node()

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

    def _case_memory_fallback_adjudication_node(self):
        def _node(state: DS160GraphState) -> DS160GraphState:
            case_board = self._payload(state.case_state.get("case_board"))
            case_memory = self._payload(state.case_state.get("case_memory"))
            next_move = self._payload(
                case_board.get("next_move") or case_memory.get("next_move")
            )
            conflicts = self._list_payload(
                case_memory.get("conflicts") or case_board.get("conflicts")
            )
            proof_points = self._list_payload(
                case_memory.get("proof_points")
                or case_board.get("proof_points")
                or case_board.get("open_proof_points")
            )
            latest_material = self._payload(case_board.get("latest_material"))

            if conflicts:
                conflict = conflicts[0]
                response = fake_adjudication_node(
                    assistant_message=self._question_from_conflict(conflict),
                    decision=GovernorDecision.HIGH_RISK_REVIEW.value,
                    next_safe_action="ask_clarification",
                )(state)
                return self._tag_case_memory_fallback(
                    response,
                    reason="case_memory_conflict",
                )

            if next_move:
                move_type = self._string_or_none(next_move.get("move_type")) or "ask"
                decision = self._decision_for_next_move(move_type)
                requested_documents = (
                    self._evidence_refs_to_requested_documents(next_move)
                    if move_type == "ask"
                    else []
                )
                response = fake_adjudication_node(
                    assistant_message=self._question_from_next_move(
                        next_move,
                        move_type=move_type,
                    ),
                    decision=decision,
                    requested_documents=requested_documents,
                    next_safe_action=self._next_safe_action_for_move(move_type),
                )(state)
                return self._tag_case_memory_fallback(
                    response,
                    reason="case_board_next_move",
                )

            if proof_points:
                proof_point = proof_points[0]
                response = fake_adjudication_node(
                    assistant_message=(
                        self._string_or_none(proof_point.get("question"))
                        or "请补充说明这个待核实事实。"
                    ),
                    decision="continue_interview",
                    next_safe_action="continue_interview",
                )(state)
                return self._tag_case_memory_fallback(
                    response,
                    reason="case_memory_proof_point",
                )

            if latest_material:
                status = self._string_or_none(
                    latest_material.get("understanding_status")
                )
                if status in {"queued", "processing"}:
                    message = "案例理解正在更新中。你可以先继续说明你的学习计划和资金安排。"
                elif status == "failed":
                    message = "这份材料暂时无法完成案例理解。你可以继续面签对话，我会先基于已知事实追问。"
                else:
                    message = "材料已经加入案例理解。请继续说明它和你的签证计划有什么关系。"
                response = fake_adjudication_node(
                    assistant_message=message,
                    decision="continue_interview",
                    next_safe_action="continue_interview",
                )(state)
                return self._tag_case_memory_fallback(
                    response,
                    reason="latest_material_status",
                )

            response = fake_adjudication_node(
                assistant_message="为什么选择去美国读这个项目？",
                decision="continue_interview",
                next_safe_action="continue_interview",
            )(state)
            return self._tag_case_memory_fallback(
                response,
                reason="no_case_memory_yet",
            )

        return _node

    def _tag_case_memory_fallback(
        self,
        state: DS160GraphState,
        *,
        reason: str,
    ) -> DS160GraphState:
        result = dict(state.adjudication_result or {})
        result["metadata"] = {
            **self._payload(result.get("metadata")),
            "fallback_used": True,
            "fallback_reason": reason,
            "case_memory_fallback": True,
        }
        return state.model_copy(update={"adjudication_result": result})

    def _decision_for_next_move(self, move_type: str) -> str:
        if move_type == "clarify_conflict":
            return GovernorDecision.HIGH_RISK_REVIEW.value
        if move_type == "probe_risk":
            return GovernorDecision.HIGH_RISK_REVIEW.value
        if move_type == "simulate_refusal":
            return GovernorDecision.SIMULATED_REFUSAL.value
        return "continue_interview"

    def _next_safe_action_for_move(self, move_type: str) -> str:
        if move_type in {"clarify_conflict", "probe_risk"}:
            return "ask_clarification"
        if move_type == "simulate_refusal":
            return "end_session"
        return "continue_interview"

    def _evidence_refs_to_requested_documents(
        self,
        next_move: dict[str, Any],
    ) -> list[str]:
        metadata = self._payload(next_move.get("metadata"))
        requested = self._string_list(metadata.get("requested_documents"))
        return requested[:1]

    def _question_from_conflict(self, conflict: dict[str, Any]) -> str:
        suggested = self._string_or_none(conflict.get("suggested_followup"))
        if suggested and not suggested.casefold().startswith("ask the applicant"):
            return suggested

        summary = self._string_or_none(conflict.get("summary")) or ""
        field_label = "关键事实"
        if "/funding/primary_source" in summary or "funding" in summary.casefold():
            field_label = "资金来源"
        elif "/education/school_name" in summary or "school" in summary.casefold():
            field_label = "学校信息"

        values = ""
        marker = "conflicting values:"
        marker_index = summary.casefold().find(marker)
        if marker_index >= 0:
            values = summary[marker_index + len(marker) :].strip(" .")
        value_suffix = f"（{values}）" if values else ""
        return (
            f"{field_label}存在不一致{value_suffix}。"
            "请说明哪个说法准确，以及为什么回答和材料会不同。"
        )

    def _question_from_next_move(
        self,
        next_move: dict[str, Any],
        *,
        move_type: str,
    ) -> str:
        question = self._string_or_none(next_move.get("question"))
        if question and not question.casefold().startswith("ask the applicant"):
            return question
        if move_type == "clarify_conflict":
            return "当前回答和材料存在不一致。请说明哪个说法准确，以及为什么会不同。"
        if move_type == "probe_risk":
            return "当前案例有一个高风险点需要先核验。请说明具体背景和原因。"
        if move_type == "simulate_refusal":
            return "当前事实已足以模拟一次高风险拒签结果，我会先说明原因和下一步。"
        return "请继续说明这个材料如何支持你的签证案例。"

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
                case_memory_snapshot=self.case_memory.get_or_build_snapshot(
                    record.session_id
                ).model_dump(mode="json"),
                evidence_graph=self.case_memory.query_evidence_graph(record.session_id),
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

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_list(self, value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        normalized: list[str] = []
        for item in value:
            text = self._string_or_none(item)
            if text is not None and text not in normalized:
                normalized.append(text)
        return normalized
