from __future__ import annotations

from app.domain.agent_runtime import (
    CitationBundle,
    CitationRef,
    DS160GraphState,
    GraphRunResult,
    PublicClaim,
)
from app.evals.graph_replay_eval import GraphReplayEvaluator, GraphReplayFixture
from app.services.agent_runtime_graph import (
    DeterministicDS160TurnGraph,
    fake_adjudication_node,
    fake_guard_node,
)


def _citation() -> CitationRef:
    return CitationRef(
        citation_id="cite-i20-school",
        source_type="case_evidence",
        source_authority="user_provided",
        source_id="source-session",
        document_id="doc-i20",
        chunk_id="chunk-school",
        span_start=0,
        span_end=50,
        content_hash="sha256:school",
        quote_or_summary="I-20 shows Example University.",
        claim_ids=["claim-school"],
    )


def test_graph_replay_eval_passes_deterministic_graph_output() -> None:
    citation = _citation()
    graph = DeterministicDS160TurnGraph(
        nodes={
            "adjudicate": fake_adjudication_node(
                assistant_message="I-20 显示 Example University，请解释这个差异。",
                decision="high_risk_review",
            ),
            "deterministic_grounding_guard": fake_guard_node(),
        }
    )
    state, events = graph.run(
        session_id="sess-1",
        run_id="run-1",
        message_text="哪里不一致？",
        citation_bundle=CitationBundle(citations=[citation]),
    )
    assert state.final_response is not None
    state = state.model_copy(
            update={
                "final_response": state.final_response.model_copy(
                    update={
                        "assistant_message": (
                            "I-20 显示 Example University，但你刚才说的是纽约大学。"
                            "请解释这个学校差异。"
                        ),
                        "public_claims": [
                            PublicClaim(
                                claim_id="claim-school",
                                claim_type="case_evidence",
                                text="I-20 显示 Example University。",
                                citation_ids=[citation.citation_id],
                            )
                        ],
                        "next_safe_action": "ask_clarification",
                    }
                )
            }
        )

    result = GraphReplayEvaluator().evaluate(
        fixture_id="school-mismatch-where",
        state=state,
        events=events,
    )

    assert result.passed is True
    assert result.failed_checks == []


def test_graph_replay_eval_flags_repeated_template() -> None:
    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        case_state={
            "recent_assistant_messages": [
                {"content": "你的说法和材料不一致，请解释。"},
                {"content": "你的说法和材料不一致，请解释。"},
            ]
        },
        final_response=GraphRunResult(
            assistant_message="你的说法和材料不一致，请解释。",
            decision="high_risk_review",
        ),
    )
    events = [
        DeterministicDS160TurnGraph()._event(
            "final",
            state=state,
            sequence=0,
            payload={"final_response": state.final_response.model_dump(mode="json")},
        )
    ]

    result = GraphReplayEvaluator().evaluate(
        fixture_id="repeated-template",
        state=state,
        events=events,
    )

    assert result.passed is False
    assert "repeated_template" in {check.name for check in result.failed_checks}


def test_graph_replay_eval_requires_high_risk_what_why_next() -> None:
    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        final_response=GraphRunResult(
            assistant_message="This case needs additional review before the interview can continue.",
            decision="high_risk_review",
            guard_status="passed",
            next_safe_action="ask_clarification",
        ),
    )
    events = [
        DeterministicDS160TurnGraph()._event(
            "final",
            state=state,
            sequence=0,
            payload={"final_response": state.final_response.model_dump(mode="json")},
        )
    ]

    result = GraphReplayEvaluator().evaluate(
        fixture_id="generic-high-risk",
        state=state,
        events=events,
    )

    assert result.passed is False
    assert "high_risk_what_why_next" in {
        check.name for check in result.failed_checks
    }


def test_graph_replay_eval_requires_failure_diagnostics() -> None:
    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        final_response=GraphRunResult(
            assistant_message="请继续说明。",
            assistant_message_author="deterministic_safe_fallback",
            decision="continue_interview",
            guard_status="fallback_required",
            incomplete_reason="provider_error",
        ),
    )
    events = [
        DeterministicDS160TurnGraph()._event(
            "final",
            state=state,
            sequence=0,
            payload={"final_response": state.final_response.model_dump(mode="json")},
        )
    ]

    result = GraphReplayEvaluator().evaluate(
        fixture_id="fallback-without-diagnostics",
        state=state,
        events=events,
    )

    assert result.passed is False
    assert "failure_diagnostics" in {check.name for check in result.failed_checks}


def test_graph_replay_eval_requires_single_final_event() -> None:
    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        final_response=GraphRunResult(
            assistant_message="请继续说明。",
            decision="continue_interview",
        ),
    )

    result = GraphReplayEvaluator().evaluate(
        fixture_id="missing-final-event",
        state=state,
        events=[],
    )

    assert result.passed is False
    assert result.failed_checks[0].name == "single_final_event"


def test_graph_replay_fixture_file_loads_and_evaluates() -> None:
    fixture = GraphReplayFixture.from_file(
        "fixtures/graph_replay/school_mismatch_where.json"
    )

    result = GraphReplayEvaluator().evaluate(
        fixture_id=fixture.fixture_id,
        state=fixture.state,
        events=fixture.events,
    )

    assert fixture.fixture_id == "school-mismatch-where"
    assert fixture.expected["checks"]
    assert result.passed is True


def test_graph_replay_eval_passes_ai_native_required_scenarios() -> None:
    evaluator = GraphReplayEvaluator()
    fixture_paths = [
        "fixtures/graph_replay/no_material_chat_starts.json",
        "fixtures/graph_replay/visual_i20_updates_case_memory.json",
        "fixtures/graph_replay/funding_claim_conflict.json",
        "fixtures/graph_replay/high_risk_simulation_without_full_package.json",
        "fixtures/graph_replay/ocr_not_used_for_applicant_image.json",
    ]

    results = [
        evaluator.evaluate_fixture_file(path)
        for path in fixture_paths
    ]

    assert [result.fixture_id for result in results] == [
        "no-material-chat-starts",
        "visual-i20-updates-case-memory",
        "funding-claim-conflict",
        "high-risk-simulation-without-full-package",
        "ocr-not-used-for-applicant-image",
    ]
    assert all(result.passed for result in results)


def test_graph_replay_eval_flags_gate_blocking_no_material_chat() -> None:
    state = DS160GraphState(
        session_id="sess-1",
        run_id="run-1",
        case_state={"assert_chat_starts_without_materials": True},
        final_response=GraphRunResult(
            assistant_message="请先补齐材料再继续。",
            decision="need_more_evidence",
            requested_documents=["i20"],
        ),
    )
    events = [
        DeterministicDS160TurnGraph()._event(
            "final",
            state=state,
            sequence=0,
            payload={"final_response": state.final_response.model_dump(mode="json")},
        )
    ]

    result = GraphReplayEvaluator().evaluate(
        fixture_id="gate-blocked-no-material-chat",
        state=state,
        events=events,
    )

    assert result.passed is False
    assert "chat_not_blocked_by_gate" in {
        check.name for check in result.failed_checks
    }
