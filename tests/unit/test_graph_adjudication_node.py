from __future__ import annotations

import json

from app.db.models import SessionRecord, SessionTurnRecord
from app.domain.agent_runtime import DS160GraphState, GraphRunResult
from app.services.graph_adjudication_node import GraphAdjudicationNode
from app.services.interview_case_state_builder import InterviewCaseStateBuilder
from app.services.llm_node_runner import LLMNodeRequest, LLMNodeResponse


class StubModelFactory:
    def __init__(self, model=None, runtime: dict | None = None) -> None:
        self.model = model
        self.runtime = runtime or {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        }

    def build(self, *args, **kwargs):
        return self.model, dict(self.runtime)


def test_graph_adjudication_node_falls_back_without_model_config() -> None:
    runtime = {
        "provider": "openai_compatible",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
        "model_unavailable_missing_env_vars": ["OPENAI_API_KEY", "OPENAI_BASE_URL"],
    }
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-fallback",
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=None, runtime=runtime)
    ).run(
        state,
        message_text="I will study computer science.",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message_author == (
        "deterministic_safe_fallback"
    )
    assert result.state.final_response.guard_status == "fallback_required"
    assert result.state.retry_budget.llm_calls_used == 0
    assert result.metadata["fallback_used"] is True
    assert result.metadata["fallback_reason"] == "model_unavailable"
    assert result.metadata["missing_env_vars"] == ["OPENAI_API_KEY", "OPENAI_BASE_URL"]


def test_graph_adjudication_fallback_uses_case_board_next_move() -> None:
    runtime = {
        "provider": "openai_compatible",
        "model": "gpt-5.4",
        "model_unavailable_missing_env_vars": ["OPENAI_API_KEY"],
    }
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-case-board-fallback",
        case_state={
            "case_board": {
                "next_move": {
                    "move_type": "ask",
                    "question": "I-20 显示 Example University。为什么选择这个项目？",
                    "reason": "学校信息已有材料证据，下一步核验选择原因。",
                    "claim_refs": ["claim-school"],
                    "evidence_refs": ["ev-school"],
                }
            }
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=None, runtime=runtime)
    ).run(
        state,
        message_text="I uploaded my I-20.",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "I-20 显示 Example University。为什么选择这个项目？"
    )
    assert result.state.final_response.assistant_message_author == (
        "deterministic_safe_fallback"
    )
    assert result.state.final_response.decision == "continue_interview"
    assert result.metadata["fallback_reason"] == "model_unavailable"


def test_graph_adjudication_fallback_clarifies_case_memory_conflict() -> None:
    runtime = {
        "provider": "openai_compatible",
        "model": "gpt-5.4",
        "model_unavailable_missing_env_vars": ["OPENAI_API_KEY"],
    }
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-conflict-fallback",
        case_state={
            "case_memory": {
                "conflicts": [
                    {
                        "conflict_id": "conflict-funding-primary-source",
                        "claim_ids": ["claim-user-funding", "claim-material-funding"],
                        "evidence_ids": ["ev-user-funding", "ev-bank-parents"],
                        "summary": "Funding source conflicts: self vs parents.",
                        "severity": "medium",
                        "suggested_followup": (
                            "Ask the applicant to reconcile the stated answer "
                            "with the uploaded evidence."
                        ),
                    }
                ]
            }
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=None, runtime=runtime)
    ).run(
        state,
        message_text="I am self-funded.",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.decision == "high_risk_review"
    assert result.state.final_response.next_safe_action == "ask_clarification"
    assert "不一致" in result.state.final_response.assistant_message
    assert result.metadata["fallback_reason"] == "model_unavailable"


def test_graph_adjudication_node_returns_typed_graph_run_result(monkeypatch) -> None:
    expected = GraphRunResult(
        assistant_message="第一年的学费和生活费由谁支付？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-typed",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return expected

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="My parents will pay.",
        declared_family="f1",
    )

    assert result.state.final_response == expected
    assert result.state.retry_budget.llm_calls_used == 1
    assert result.metadata == {
        "status": "completed",
        "assistant_message_author": "adjudication_agent",
        "provider": "openai_compatible",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
        "fallback_used": False,
        "llm_calls_used": 1,
    }


def test_graph_adjudication_node_falls_back_on_provider_error(monkeypatch) -> None:
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-provider-error",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        raise RuntimeError("upstream broke")

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="My parents will pay.",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message_author == (
        "deterministic_safe_fallback"
    )
    assert result.state.final_response.guard_status == "fallback_required"
    assert result.metadata["fallback_reason"] == "provider_error"
    assert result.metadata["error_type"] == "ModelRuntimeError"
    assert result.metadata["status_code"] == 503


def test_graph_adjudication_node_accepts_legacy_factory_signature() -> None:
    class LegacyFactory:
        def build(self, module_key, stage_key):
            assert module_key == "adjudication_agent"
            assert stage_key == "interview_turn"
            return None, {
                "provider": "openai_compatible",
                "model": "gpt-5.4",
                "model_unavailable_missing_env_vars": [],
            }

    result = GraphAdjudicationNode(model_factory=LegacyFactory()).run(
        DS160GraphState(
            session_id="sess-graph-adjudication",
            run_id="graph-run-legacy-factory",
        ),
        message_text="hello",
        declared_family="f1",
    )

    assert result.metadata["fallback_reason"] == "model_unavailable"


def test_graph_adjudication_prompt_uses_sanitized_case_state(monkeypatch) -> None:
    class CapturingRunner:
        def __init__(self) -> None:
            self.requests: list[LLMNodeRequest] = []

        def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
            self.requests.append(request)
            return LLMNodeResponse(
                output=GraphRunResult(
                    assistant_message="请继续说明你的学习计划。",
                    assistant_message_author="adjudication_agent",
                    decision="continue_interview",
                ),
                metadata={},
            )

    runner = CapturingRunner()
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-sanitized-prompt",
        case_state={
            "documents": [
                {
                    "document_id": "doc-debug",
                    "artifact": {
                        "document_type": "i20",
                        "metadata": {"debug_material_bundle": True},
                    },
                }
            ],
            "evidence_items": [],
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object()),
        llm_runner=runner,
    ).run(
        state,
        message_text="materials_updated",
        declared_family="f1",
    )

    assert result.metadata["status"] == "completed"
    assert len(runner.requests) == 1
    prompt = runner.requests[0].prompt
    prompt_payload = json.loads(prompt)
    assert prompt_payload["case_state"] == state.case_state
    assert prompt_payload["user"] == "materials_updated"
    serialized_prompt = prompt
    assert "debug_material_bundle" in serialized_prompt
    assert "expected_findings" not in serialized_prompt
    assert "synthetic_bundle_id" not in serialized_prompt
    assert "dbg-bundle-" not in serialized_prompt
    assert "debug_bundle_scenario" not in serialized_prompt
    assert "school_mismatch_bundle" not in serialized_prompt
    assert "学校材料冲突包" not in serialized_prompt


def test_graph_adjudication_prompt_uses_bounded_transcript_window() -> None:
    class CapturingRunner:
        def __init__(self) -> None:
            self.requests: list[LLMNodeRequest] = []

        def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
            self.requests.append(request)
            return LLMNodeResponse(
                output=GraphRunResult(
                    assistant_message="请继续说明你的学习计划。",
                    assistant_message_author="adjudication_agent",
                    decision="continue_interview",
                ),
                metadata={},
            )

    runner = CapturingRunner()
    full_transcript = [
        {
            "turn_id": f"turn-{index}",
            "turn_index": index,
            "role": "user" if index % 2 else "assistant",
            "content": f"message-{index}",
        }
        for index in range(1, 41)
    ]
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-windowed-transcript",
        case_state={
            "full_transcript": full_transcript,
            "transcript": {
                "turn_count": len(full_transcript),
                "roles": {"user": 20, "assistant": 20},
            },
            "history_summary": {"turn_count": len(full_transcript)},
        },
    )

    GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object()),
        llm_runner=runner,
    ).run(
        state,
        message_text="continue",
        declared_family="f1",
    )

    prompt_payload = json.loads(runner.requests[0].prompt)
    prompt_case_state = prompt_payload["case_state"]
    assert prompt_case_state["full_transcript"] == full_transcript[-24:]
    assert prompt_case_state["transcript"]["turn_count"] == 40
    assert prompt_case_state["transcript"]["prompt_window"] == {
        "strategy": "tail_window",
        "source": "full_transcript",
        "retained_turn_count": 24,
        "omitted_turn_count": 16,
        "max_turns": 24,
        "max_chars": 12000,
    }
    assert state.case_state["full_transcript"] == full_transcript


def test_graph_adjudication_instructions_guard_against_redundant_material_questions(
    monkeypatch,
) -> None:
    class CapturingRunner:
        def __init__(self) -> None:
            self.requests: list[LLMNodeRequest] = []

        def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
            self.requests.append(request)
            return LLMNodeResponse(
                output=GraphRunResult(
                    assistant_message="毕业后你准备做什么工作？",
                    assistant_message_author="adjudication_agent",
                    decision="continue_interview",
                ),
                metadata={},
            )

    runner = CapturingRunner()
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-known-facts",
        case_state={
            "case_brief": {
                "known_documented_facts": [
                    {
                        "field_path": "/education/program_name",
                        "label": "项目",
                        "value": "Master of Example Analytics",
                    }
                ],
                "recent_assistant_questions": [
                    {"question": "你本科读的是什么专业？"}
                ],
                "latest_user_referred_to_materials": True,
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(
            model=object(),
            runtime={
                "provider": "openai_compatible",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "instructions": "BASE INSTRUCTIONS",
            },
        ),
        llm_runner=runner,
    ).run(
        state,
        message_text="我提供的资料里面都有",
        declared_family="f1",
    )

    assert result.metadata["status"] == "completed"
    assert len(runner.requests) == 1
    instructions = runner.requests[0].instructions
    assert "known_documented_facts" in instructions
    assert "Do not ask for those facts as if they were missing" in instructions
    assert "Do not repeat the same question" in instructions
    prompt_payload = json.loads(runner.requests[0].prompt)
    assert prompt_payload["case_state"] == state.case_state


def test_graph_adjudication_prompt_includes_case_memory_and_case_board() -> None:
    class CapturingRunner:
        def __init__(self) -> None:
            self.requests: list[LLMNodeRequest] = []

        def run(self, request: LLMNodeRequest) -> LLMNodeResponse:
            self.requests.append(request)
            return LLMNodeResponse(
                output=GraphRunResult(
                    assistant_message="为什么选择 Example University？",
                    assistant_message_author="adjudication_agent",
                    decision="continue_interview",
                ),
                metadata={},
            )

    runner = CapturingRunner()
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-case-memory",
        case_state={
            "case_memory": {
                "claims": [
                    {
                        "claim_id": "claim-school",
                        "field_path": "/education/school_name",
                        "value": "Example University",
                        "status": "documented",
                    }
                ],
                "evidence_cards": [
                    {
                        "evidence_id": "ev-school",
                        "excerpt": "School Name: Example University",
                    }
                ],
            },
            "case_board": {
                "latest_material": {
                    "document_id": "doc-i20",
                    "understanding_status": "completed",
                }
            },
        },
    )

    GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object()),
        llm_runner=runner,
    ).run(
        state,
        message_text="我上传了 I-20。",
        declared_family="f1",
    )

    prompt_payload = json.loads(runner.requests[0].prompt)
    assert prompt_payload["case_state"]["case_memory"]["claims"][0]["value"] == (
        "Example University"
    )
    assert prompt_payload["case_state"]["case_board"]["latest_material"][
        "understanding_status"
    ] == "completed"


def test_graph_adjudication_repairs_repeated_question_after_material_reference(
    monkeypatch,
) -> None:
    repeated = GraphRunResult(
        assistant_message="请回答我的问题：你本科读的是什么专业？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return repeated

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-repair-repeat",
        case_state={
            "case_brief": {
                "recent_assistant_questions": [
                    {"question": "你本科读的是什么专业？"}
                ],
                "latest_user_referred_to_materials": True,
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="我提供的资料里面都有",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "材料我看到了。毕业后你准备做什么工作？"
    )
    assert result.metadata["question_repaired"] is True
    assert result.metadata["question_repair_reason"] == "repeated_recent_question"


def test_graph_adjudication_repairs_semantically_repeated_post_study_question(
    monkeypatch,
) -> None:
    repeated = GraphRunResult(
        assistant_message="毕业后你准备回国做什么工作？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return repeated

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-repair-topic-repeat",
        case_state={
            "case_brief": {
                "recent_assistant_questions": [
                    {"question": "毕业后你准备做什么工作？"},
                    {"question": "毕业后你回国准备做什么岗位？"},
                ],
                "latest_user_referred_to_materials": False,
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="我已经回答过你了",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "为什么不在国内读同类项目？"
    )
    assert result.metadata["question_repaired"] is True
    assert result.metadata["question_repair_reason"] == "repeated_recent_question"


def test_graph_adjudication_repairs_question_for_answered_topic_outside_recent_window(
    monkeypatch,
) -> None:
    repeated = GraphRunResult(
        assistant_message="毕业后你准备回国做什么工作？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return repeated

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-repair-answered-topic",
        case_state={
            "case_brief": {
                "answered_topic_keys": ["post_study_plan"],
                "recent_assistant_questions": [
                    {"question": "第一年费用的资金来源是什么？"}
                ],
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="我已经回答过毕业后的计划了",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "为什么不在国内读同类项目？"
    )
    assert result.metadata["question_repaired"] is True
    assert result.metadata["question_repair_reason"] == "answered_topic_repeated"


def test_graph_adjudication_repairs_answered_topic_without_recent_questions(
    monkeypatch,
) -> None:
    repeated = GraphRunResult(
        assistant_message="毕业后你准备回国做什么工作？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return repeated

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-repair-answered-topic-no-recent",
        case_state={
            "case_brief": {
                "answered_topic_keys": ["post_study_plan"],
                "recent_assistant_questions": [],
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="我已经回答过毕业后的计划了",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "为什么不在国内读同类项目？"
    )
    assert result.metadata["question_repaired"] is True
    assert result.metadata["question_repair_reason"] == "answered_topic_repeated"


def test_graph_adjudication_keeps_sponsor_job_distinct_from_post_study_plan(
    monkeypatch,
) -> None:
    result_from_llm = GraphRunResult(
        assistant_message="你父亲做什么工作？",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return result_from_llm

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    state = DS160GraphState(
        session_id="sess-graph-adjudication",
        run_id="graph-run-sponsor-topic",
        case_state={
            "case_brief": {
                "recent_assistant_questions": [
                    {"question": "毕业后你准备做什么工作？"}
                ],
            },
        },
    )

    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="由我父亲支付",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == "你父亲做什么工作？"
    assert "question_repaired" not in result.metadata


def test_graph_adjudication_golden_replay_does_not_repeat_answered_school_program_funding(
    monkeypatch,
) -> None:
    repeated = GraphRunResult(
        assistant_message="Who will fund your education?",
        assistant_message_author="adjudication_agent",
        decision="continue_interview",
    )

    def fake_run_agent(self, *, model, runtime, state, message_text):
        return repeated

    monkeypatch.setattr(GraphAdjudicationNode, "_run_agent", fake_run_agent)
    record = SessionRecord(
        session_id="sess-golden-repeat",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-assistant-school",
            turn_index=1,
            session_id=record.session_id,
            role="assistant",
            content="Which school will you attend?",
            source="graph_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-school",
            turn_index=2,
            session_id=record.session_id,
            role="user",
            content="I will attend Example University.",
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-program",
            turn_index=3,
            session_id=record.session_id,
            role="assistant",
            content="What program will you study?",
            source="graph_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-program",
            turn_index=4,
            session_id=record.session_id,
            role="user",
            content="I will study the Master of Computer Science program.",
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-funding",
            turn_index=5,
            session_id=record.session_id,
            role="assistant",
            content="Who will fund your first year tuition and living costs?",
            source="graph_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-funding",
            turn_index=6,
            session_id=record.session_id,
            role="user",
            content="My parents will pay my tuition and living costs.",
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-academic",
            turn_index=7,
            session_id=record.session_id,
            role="assistant",
            content="What academic experience prepared you for this program?",
            source="graph_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-academic",
            turn_index=8,
            session_id=record.session_id,
            role="user",
            content="My computer engineering coursework prepared me for it.",
            source="user_message",
            metadata_json={},
        ),
    ]
    case_state = InterviewCaseStateBuilder(max_recent_turns=1).build(record, turns)
    assert case_state["transcript"]["turn_count"] == len(turns)
    assert case_state["case_brief"]["recent_assistant_questions"] == [
        {
            "turn_id": "turn-assistant-academic",
            "turn_index": 7,
            "question": "What academic experience prepared you for this program?",
        }
    ]
    assert set(case_state["case_brief"]["answered_topic_keys"]) >= {
        "program_school",
        "funding",
    }

    state = DS160GraphState(
        session_id=record.session_id,
        run_id="graph-run-golden-repeat",
        case_state=case_state,
    )
    result = GraphAdjudicationNode(
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="I already answered my school, program, and funding plan.",
        declared_family="f1",
    )

    assert result.state.final_response is not None
    assert result.state.final_response.assistant_message == (
        "毕业后你准备做什么工作？"
    )
    assert result.metadata["question_repaired"] is True
    assert result.metadata["question_repair_reason"] == "answered_topic_repeated"
