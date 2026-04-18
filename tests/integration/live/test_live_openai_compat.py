import pytest

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner


@pytest.mark.live_llm
def test_live_openai_compat_maps_to_domain_flow(
    live_api_client,
    monkeypatch,
) -> None:
    build_calls: list[tuple[str, str, str | None]] = []
    run_calls: list[str] = []
    original_build = AgentModelFactory.build
    original_run = QuestionAgentRunner.run

    def tracked_build(self, module_key, stage_key):
        model, runtime = original_build(self, module_key, stage_key)
        if module_key == "question_agent":
            build_calls.append((module_key, stage_key, runtime.get("model")))
        return model, runtime

    def tracked_run(self, *, deps, profile_payload, score_payload, governor_decision):
        run_calls.append(deps.session_id)
        return original_run(
            self,
            deps=deps,
            profile_payload=profile_payload,
            score_payload=score_payload,
            governor_decision=governor_decision,
        )

    monkeypatch.setattr(AgentModelFactory, "build", tracked_build)
    monkeypatch.setattr(QuestionAgentRunner, "run", tracked_run)
    response = live_api_client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "user",
                    "content": "My mother and father will cover all my tuition and living expenses.",
                }
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["choices"][0]["message"]["role"] == "assistant"
    assert payload["choices"][0]["message"]["content"]
    assert payload["metadata"]["phase_state"] == "intake"
    assert build_calls == [("question_agent", "interview_turn", "gpt-5.4")]
    assert len(run_calls) == 1
