import pytest

from app.agents.adjudication_agent import AdjudicationAgentRunner
from app.agents.model_factory import AgentModelFactory


@pytest.mark.live_llm
def test_live_responses_api_reuses_session_via_previous_response_id(
    live_api_client,
    live_expected_runtime_model,
    monkeypatch,
) -> None:
    build_calls: list[tuple[str, str, str | None]] = []
    run_calls: list[str] = []
    original_build = AgentModelFactory.build
    original_run = AdjudicationAgentRunner.run

    def tracked_build(self, module_key, stage_key, declared_family=None):
        model, runtime = original_build(
            self,
            module_key,
            stage_key,
            declared_family=declared_family,
        )
        if module_key == "adjudication_agent":
            build_calls.append((module_key, stage_key, runtime.get("model")))
        return model, runtime

    def tracked_run(
        self,
        *,
        deps,
        dynamic_turn_context,
        tool_outputs=None,
        user_message,
        boundary_decision,
    ):
        assert dynamic_turn_context["prompt_roles"]["system"] == "stable_policy"
        assert user_message
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            dynamic_turn_context=dynamic_turn_context,
            tool_outputs=tool_outputs,
            user_message=user_message,
            boundary_decision=boundary_decision,
        )

    monkeypatch.setattr(AgentModelFactory, "build", tracked_build)
    monkeypatch.setattr(AdjudicationAgentRunner, "run", tracked_run)

    first_response = live_api_client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "My parents will pay for my first year of study.",
            "metadata": {"declared_family": "f1"},
        },
    )

    assert first_response.status_code == 200
    first_payload = first_response.json()
    session_id = first_payload["metadata"]["session_id"]
    assert first_payload["id"].startswith(f"resp-{session_id}-")
    assert first_payload["output_text"]
    assert first_payload["metadata"]["context_mode"] == "new_session"

    second_response = live_api_client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "previous_response_id": first_payload["id"],
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "I will study data science at Example University.",
                        }
                    ],
                }
            ],
        },
    )

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert second_payload["metadata"]["session_id"] == session_id
    assert second_payload["metadata"]["context_mode"] == "previous_response"
    assert second_payload["output_text"]
    assert build_calls
    assert build_calls[-1] == (
        "adjudication_agent",
        "interview_turn",
        live_expected_runtime_model("adjudication_agent", "interview_turn"),
    )
    assert run_calls == [session_id, session_id]
