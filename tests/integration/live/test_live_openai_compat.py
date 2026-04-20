import pytest
from sqlalchemy import func, select

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.db.models import SessionRecord
from app.workers.parse_worker import ParseWorker


@pytest.mark.live_llm
def test_live_openai_compat_maps_to_domain_flow(
    live_api_client,
    live_expected_runtime_model,
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
    assert payload["metadata"]["session_id"].startswith("sess-")
    assert payload["metadata"]["context_mode"] == "new_session"
    assert payload["metadata"]["phase_state"] == "gate_review"
    assert build_calls == [
        (
            "question_agent",
            "interview_turn",
            live_expected_runtime_model("question_agent", "interview_turn"),
        )
    ]
    assert len(run_calls) == 1


@pytest.mark.live_llm
def test_live_openai_compat_reuses_session_after_upload_and_parse(
    live_api_client,
    live_db_session_factory,
    live_build_pdf_bytes,
    live_expected_runtime_model,
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

    first_completion = live_api_client.post(
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

    assert first_completion.status_code == 200
    first_payload = first_completion.json()
    session_id = first_payload["metadata"]["session_id"]
    assert first_payload["metadata"] == {
        "session_id": session_id,
        "phase_state": "gate_review",
        "context_mode": "new_session",
    }

    upload_response = live_api_client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.pdf",
                live_build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )

    assert upload_response.status_code == 202

    second_completion = live_api_client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "assistant",
                    "content": first_payload["choices"][0]["message"]["content"],
                },
                {"role": "user", "content": "I will study computer science."},
            ],
            "metadata": {"session_id": session_id},
        },
    )

    assert second_completion.status_code == 200
    second_payload = second_completion.json()
    assert second_payload["metadata"] == {
        "session_id": session_id,
        "phase_state": "gate_review",
        "context_mode": "existing_session",
    }

    with live_db_session_factory() as db:
        processed_any = False
        while ParseWorker(db).run_once():
            processed_any = True
    assert processed_any is True

    third_completion = live_api_client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "assistant",
                    "content": second_payload["choices"][0]["message"]["content"],
                },
                {"role": "user", "content": "I will study computer science."},
            ],
            "metadata": {"session_id": session_id},
        },
    )

    assert third_completion.status_code == 200
    third_payload = third_completion.json()
    assert third_payload["metadata"] == {
        "session_id": session_id,
        "phase_state": "interview",
        "context_mode": "existing_session",
    }
    assert third_payload["choices"][0]["message"]["role"] == "assistant"
    assert third_payload["choices"][0]["message"]["content"]

    user_report_response = live_api_client.get(f"/v1/sessions/{session_id}/reports/user")

    assert user_report_response.status_code == 200
    user_report = user_report_response.json()
    assert user_report["interview_status"] == "continue_interview"
    assert user_report["current_key_question"] == third_payload["choices"][0]["message"][
        "content"
    ]
    assert build_calls
    assert build_calls[-1] == (
        "question_agent",
        "interview_turn",
        live_expected_runtime_model("question_agent", "interview_turn"),
    )
    assert run_calls
    assert run_calls[-1] == session_id
    assert set(run_calls) == {session_id}
    with live_db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
    assert session_count == 1
