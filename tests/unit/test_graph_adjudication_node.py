from __future__ import annotations

import json

from app.domain.agent_runtime import DS160GraphState, GraphRunResult
from app.services.graph_adjudication_node import GraphAdjudicationNode


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
    prompts: list[str] = []

    class CapturingAgent:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_sync(self, prompt: str):
            prompts.append(prompt)

            class Result:
                output = GraphRunResult(
                    assistant_message="请继续说明你的学习计划。",
                    assistant_message_author="adjudication_agent",
                    decision="continue_interview",
                )

            return Result()

    monkeypatch.setattr(
        "app.services.graph_adjudication_node.Agent",
        CapturingAgent,
    )
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
        model_factory=StubModelFactory(model=object())
    ).run(
        state,
        message_text="materials_updated",
        declared_family="f1",
    )

    assert result.metadata["status"] == "completed"
    assert len(prompts) == 1
    prompt_payload = json.loads(prompts[0])
    assert prompt_payload["case_state"] == state.case_state
    assert prompt_payload["user"] == "materials_updated"
    serialized_prompt = prompts[0]
    assert "debug_material_bundle" in serialized_prompt
    assert "expected_findings" not in serialized_prompt
    assert "synthetic_bundle_id" not in serialized_prompt
    assert "dbg-bundle-" not in serialized_prompt
    assert "debug_bundle_scenario" not in serialized_prompt
    assert "school_mismatch_bundle" not in serialized_prompt
    assert "学校材料冲突包" not in serialized_prompt
