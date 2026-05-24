from datetime import datetime, timezone

import pytest

from app.domain.agent_runtime import (
    CitationBundle,
    CitationRef,
    DS160GraphState,
    GraphEvent,
    GraphRunResult,
    PublicClaim,
)
from app.services.graph_response_mapper import GraphResponseMapper


def _citation() -> CitationRef:
    return CitationRef(
        citation_id="cite-i20-school",
        source_type="case_evidence",
        source_authority="user_provided",
        source_id="session-docs",
        document_id="doc-i20",
        chunk_id="chunk-i20-school",
        span_start=0,
        span_end=31,
        content_hash="sha256:school",
        quote_or_summary="I-20 shows Example University.",
        retrieved_at=datetime(2026, 5, 24, tzinfo=timezone.utc),
        claim_ids=["claim-school"],
    )


def _events(run_id: str) -> list[GraphEvent]:
    return [
        GraphEvent(
            event_type="accepted",
            run_id=run_id,
            sequence=0,
            payload={"client_turn_id": "turn-user-1"},
        ),
        GraphEvent(
            event_type="state_built",
            run_id=run_id,
            sequence=1,
            payload={"node": "build_case_state"},
        ),
        GraphEvent(
            event_type="adjudication_completed",
            run_id=run_id,
            sequence=2,
            payload={
                "node": "adjudicate",
                "provider": "openai",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
            },
        ),
        GraphEvent(
            event_type="guard_completed",
            run_id=run_id,
            sequence=3,
            payload={"node": "deterministic_grounding_guard"},
        ),
        GraphEvent(
            event_type="final",
            run_id=run_id,
            sequence=4,
            payload={"final_response": {"assistant_message": "ok"}},
        ),
    ]


def test_graph_response_mapper_returns_legacy_compatible_fields() -> None:
    citation = _citation()
    final_response = GraphRunResult(
        assistant_message="I-20 显示 Example University，请解释你刚才说的学校差异。",
        assistant_message_author="adjudication_agent",
        decision="high_risk_review",
        public_claims=[
            PublicClaim(
                claim_id="claim-school",
                claim_type="case_evidence",
                text="I-20 显示 Example University。",
                citation_ids=[citation.citation_id],
            )
        ],
        used_citation_ids=[citation.citation_id],
        guard_status="passed",
        next_safe_action="ask_clarification",
    )
    state = DS160GraphState(
        session_id="sess-map",
        run_id="run-map",
        client_turn_id="turn-user-1",
        user_turn={"turn_id": "turn-user-1", "content": "I will attend NYU."},
        case_state={
            "gate_progress": {"overall_status": "ready_for_interview"},
            "score_history_tail": [
                {
                    "category_fit": 70,
                    "document_readiness": 80,
                    "narrative_consistency": 45,
                    "confidence": 75,
                    "risk_flags": [
                        {
                            "code": "school_mismatch",
                            "severity": "high",
                            "status": "confirmed",
                        }
                    ],
                    "missing_evidence": [],
                }
            ],
        },
        citation_bundle=CitationBundle(citations=[citation]),
        final_response=final_response,
    )

    payload = GraphResponseMapper().to_message_response(state, _events("run-map"))

    assert payload["assistant_message"] == final_response.assistant_message
    assert payload["governor_decision"] == "high_risk_review"
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"] == {"overall_status": "ready_for_interview"}
    assert payload["agent_runtime"] == "graph"
    assert payload["graph_run_id"] == "run-map"
    assert payload["graph_trace"]["event_count"] == 5
    assert payload["graph_trace"]["used_citation_ids"] == ["cite-i20-school"]
    assert payload["turn_decision"]["decision"] == "high_risk_review"
    assert payload["turn_decision"]["assistant_message_author"] == (
        "adjudication_agent"
    )
    assert payload["prompt_trace"] == {
        "prompt_pack_id": "ds160.graph_runtime",
        "prompt_version": "agent-runtime.v1",
        "graph_run_id": "run-map",
        "assistant_message_author": "adjudication_agent",
        "guard_status": "passed",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }
    assert payload["runtime_view_state"]["decision"] == "high_risk_review"
    assert payload["runtime_view_state"]["public_status"] == "high_risk_review"
    assert payload["runtime_view_state"]["current_focus"] == {
        "owner": "graph_runtime",
        "kind": "risk_review",
        "risk_code": "school_mismatch",
    }
    assert payload["turn_record"]["user_turn_id"] == "turn-user-1"
    assert payload["turn_record"]["assistant_message"] == (
        "I-20 显示 Example University，请解释你刚才说的学校差异。"
    )
    assert payload["turn_record"]["trace_refs"] == [
        "accepted",
        "build_case_state",
        "adjudicate",
        "deterministic_grounding_guard",
        "final",
    ]


def test_graph_response_mapper_never_rewrites_assistant_message() -> None:
    message = "Please upload your funding proof."
    state = DS160GraphState(
        session_id="sess-doc",
        run_id="run-doc",
        user_turn={"content": "I can provide it later."},
        final_response=GraphRunResult(
            assistant_message=message,
            assistant_message_author="deterministic_safe_fallback",
            decision="need_more_evidence",
            requested_documents=["I-20", "i20", " "],
            guard_status="fallback_required",
            incomplete_reason="guard_retry_exhausted",
            next_safe_action="request_document",
        ),
    )

    payload = GraphResponseMapper().to_message_response(state, _events("run-doc"))

    assert payload["assistant_message"] == message
    assert payload["requested_documents"] == ["i20"]
    assert payload["runtime_view_state"]["current_key_proof"] == "i20"
    assert payload["runtime_view_state"]["public_status"] == "waiting_key_proof"
    assert payload["turn_decision"]["incomplete_reason"] == "guard_retry_exhausted"
    assert payload["graph_trace"]["guard_status"] == "fallback_required"


def test_graph_response_mapper_rejects_missing_final_response() -> None:
    state = DS160GraphState(session_id="sess-missing", run_id="run-missing")

    with pytest.raises(ValueError, match="final_response"):
        GraphResponseMapper().to_message_response(state, [])


def test_graph_response_mapper_rejects_mismatched_event_run_id() -> None:
    state = DS160GraphState(
        session_id="sess-mismatch",
        run_id="run-expected",
        final_response=GraphRunResult(
            assistant_message="What will you study?",
            decision="continue_interview",
        ),
    )

    with pytest.raises(ValueError, match="belong to the mapped graph run"):
        GraphResponseMapper().to_message_response(
            state,
            [
                GraphEvent(
                    event_type="accepted",
                    run_id="run-other",
                    sequence=0,
                )
            ],
        )


def test_graph_response_mapper_does_not_depend_on_legacy_projector() -> None:
    mapper = GraphResponseMapper()

    assert not hasattr(mapper, "turn_projector")
    assert not hasattr(mapper, "capability_orchestrator")
