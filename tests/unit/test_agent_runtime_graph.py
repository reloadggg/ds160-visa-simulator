from __future__ import annotations

from app.domain.agent_runtime import CitationBundle, RetryBudget
from app.services.agent_runtime_graph import (
    DeterministicDS160TurnGraph,
    fake_adjudication_node,
    fake_guard_node,
)


def test_deterministic_graph_emits_ordered_contract_events() -> None:
    graph = DeterministicDS160TurnGraph(
        nodes={
            "adjudicate": fake_adjudication_node(
                assistant_message="你这次去美国读什么项目？"
            ),
            "deterministic_grounding_guard": fake_guard_node(),
        }
    )

    assert graph.is_official_langgraph_runtime is True
    assert graph.graph_runtime_name == "CompiledStateGraph"

    state, events = graph.run(
        session_id="sess-1",
        run_id="run-1",
        client_turn_id="client-turn-1",
        message_text="我要去读数据科学。",
        citation_bundle=CitationBundle(),
    )

    assert state.final_response is not None
    assert state.final_response.assistant_message_author == "adjudication_agent"
    assert state.final_response.assistant_message == "你这次去美国读什么项目？"
    assert state.retry_budget.llm_calls_used == 1
    assert [event.sequence for event in events] == list(range(len(events)))
    assert events[0].event_type == "accepted"
    assert events[-1].event_type == "final"
    assert events[-1].payload["final_response"]["assistant_message_author"] == (
        "adjudication_agent"
    )


def test_deterministic_graph_respects_llm_retry_budget() -> None:
    graph = DeterministicDS160TurnGraph(
        nodes={
            "adjudicate": fake_adjudication_node(),
            "deterministic_grounding_guard": fake_guard_node(),
        }
    )

    try:
        graph.run(
            session_id="sess-1",
            run_id="run-1",
            message_text="hello",
            retry_budget=RetryBudget(max_llm_calls=0),
        )
    except ValueError as exc:
        assert "LLM call retry budget exhausted" in str(exc)
    else:
        raise AssertionError("graph should stop when retry budget is exhausted")
