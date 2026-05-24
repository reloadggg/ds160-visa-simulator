from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.domain.agent_runtime import (
    CitationBundle,
    DS160GraphState,
    GraphEvent,
    GraphRunResult,
    GroundingCheckResult,
    GroundingViolation,
    RetryBudget,
)


GraphNode = Callable[[DS160GraphState], DS160GraphState]


def _replace_state(
    _current: DS160GraphState | None,
    update: DS160GraphState | None,
) -> DS160GraphState | None:
    return update or _current


def _append_events(
    current: list[GraphEvent],
    update: list[GraphEvent] | None,
) -> list[GraphEvent]:
    return current + list(update or [])


class LangGraphTurnState(TypedDict):
    state: Annotated[DS160GraphState, _replace_state]
    events: Annotated[list[GraphEvent], _append_events]
    next_sequence: int


class DeterministicDS160TurnGraph:
    """DS-160 turn graph backed by the official LangGraph StateGraph runtime."""

    node_order = (
        "receive_turn",
        "build_case_state",
        "plan_retrieval",
        "retrieve_policy_knowledge",
        "retrieve_case_evidence",
        "build_citation_bundle",
        "optional_material_review",
        "adjudicate",
        "deterministic_grounding_guard",
        "project_response",
        "persist_run",
    )

    def __init__(
        self,
        *,
        nodes: dict[str, GraphNode] | None = None,
    ) -> None:
        self.nodes = dict(nodes or {})
        self.compiled_graph = self._compile_graph()

    def run(
        self,
        *,
        session_id: str,
        run_id: str,
        message_text: str,
        client_turn_id: str | None = None,
        citation_bundle: CitationBundle | None = None,
        retry_budget: RetryBudget | None = None,
    ) -> tuple[DS160GraphState, list[GraphEvent]]:
        state = DS160GraphState(
            session_id=session_id,
            run_id=run_id,
            client_turn_id=client_turn_id,
            user_turn={"content": message_text},
            citation_bundle=citation_bundle or CitationBundle(),
            retry_budget=retry_budget or RetryBudget(),
        )
        accepted_event = self._event(
            "accepted",
            state=state,
            sequence=0,
            payload={"client_turn_id": client_turn_id},
        )
        result = self.compiled_graph.invoke(
            {
                "state": state,
                "events": [accepted_event],
                "next_sequence": 1,
            },
            config={
                "configurable": {
                    "thread_id": run_id,
                }
            },
        )
        return result["state"], result["events"]

    def _compile_graph(self) -> CompiledStateGraph:
        builder = StateGraph(LangGraphTurnState)
        for node_name in self.node_order:
            builder.add_node(node_name, self._langgraph_node(node_name))

        builder.add_edge(START, self.node_order[0])
        for start, end in zip(self.node_order, self.node_order[1:]):
            builder.add_edge(start, end)
        builder.add_edge(self.node_order[-1], END)
        return builder.compile(name="ds160_turn_graph")

    def _langgraph_node(self, node_name: str):
        def _node(graph_state: LangGraphTurnState) -> dict[str, Any]:
            state = self.nodes.get(node_name, self._noop_node)(graph_state["state"])
            event_type = self._event_type_for_node(node_name)
            if event_type is None:
                return {"state": state}
            sequence = graph_state["next_sequence"]
            event = self._event(
                event_type,
                state=state,
                sequence=sequence,
                payload=self._payload_for_node(node_name, state),
            )
            return {
                "state": state,
                "events": [event],
                "next_sequence": sequence + 1,
            }

        return _node

    @property
    def graph_runtime_name(self) -> str:
        return type(self.compiled_graph).__name__

    @property
    def is_official_langgraph_runtime(self) -> bool:
        return isinstance(self.compiled_graph, CompiledStateGraph)

    def _noop_node(self, state: DS160GraphState) -> DS160GraphState:
        return state

    def _event(
        self,
        event_type: str,
        *,
        state: DS160GraphState,
        sequence: int,
        payload: dict[str, Any],
    ) -> GraphEvent:
        return GraphEvent(
            event_type=event_type,  # type: ignore[arg-type]
            run_id=state.run_id,
            sequence=sequence,
            payload=payload,
        )

    def _event_type_for_node(self, node_name: str) -> str | None:
        mapping = {
            "receive_turn": "state_built",
            "build_case_state": "state_built",
            "plan_retrieval": "retrieval_started",
            "retrieve_policy_knowledge": "retrieval_completed",
            "retrieve_case_evidence": "retrieval_completed",
            "build_citation_bundle": "retrieval_completed",
            "optional_material_review": "material_review_completed",
            "adjudicate": "adjudication_completed",
            "deterministic_grounding_guard": "guard_completed",
            "project_response": None,
            "persist_run": "final",
        }
        return mapping[node_name]

    def _payload_for_node(
        self,
        node_name: str,
        state: DS160GraphState,
    ) -> dict[str, Any]:
        if node_name in {"project_response", "persist_run"}:
            final_response = state.final_response
            if final_response is None:
                return {"node": node_name, "final_response_pending": True}
            return {
                "final_response": final_response.model_dump(mode="json")
            }
        if node_name == "deterministic_grounding_guard":
            guard_result = state.guard_result
            return {
                "node": node_name,
                "guard_result": (
                    guard_result.model_dump(mode="json") if guard_result else {}
                )
            }
        if node_name == "adjudicate":
            payload = {"node": node_name}
            adjudication_result = state.adjudication_result
            if isinstance(adjudication_result, dict):
                metadata = adjudication_result.get("metadata")
                if isinstance(metadata, dict):
                    payload.update(metadata)
            return payload
        return {"node": node_name}


def fake_adjudication_node(
    *,
    assistant_message: str = "请继续说明你的学习计划。",
    decision: str = "continue_interview",
) -> GraphNode:
    def _node(state: DS160GraphState) -> DS160GraphState:
        budget = state.retry_budget.consume_llm_call()
        citation_ids = sorted(state.citation_bundle.citation_ids)
        response = GraphRunResult(
            assistant_message=assistant_message,
            assistant_message_author="adjudication_agent",
            decision=decision,
            used_citation_ids=citation_ids,
            guard_status="passed",
            next_safe_action="continue_interview",
        )
        return state.model_copy(
            update={
                "retry_budget": budget,
                "adjudication_result": response.model_dump(mode="json"),
                "final_response": response,
            }
        )

    return _node


def fake_guard_node(status: str = "passed") -> GraphNode:
    def _node(state: DS160GraphState) -> DS160GraphState:
        final_response = state.final_response
        if final_response is not None and final_response.guard_status != "passed":
            guard_result = GroundingCheckResult(
                status=final_response.guard_status,
                violations=[
                    GroundingViolation(
                        code=final_response.incomplete_reason or "schema_invalid",
                        detail="final response required deterministic fallback",
                    )
                ],
            )
        else:
            guard_result = GroundingCheckResult(status=status)  # type: ignore[arg-type]
        if final_response is not None and final_response.guard_status == "passed":
            final_response = final_response.model_copy(
                update={"guard_status": guard_result.status}
            )
        return state.model_copy(
            update={
                "guard_result": guard_result,
                "final_response": final_response,
            }
        )

    return _node
