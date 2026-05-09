import pytest
from sqlalchemy import func, select

from app.agents.model_factory import AgentModelFactory
from app.agents.adjudication_agent import AdjudicationAgentRunner
from app.db.models import SessionRecord
from app.workers.parse_worker import ParseWorker


def assert_live_post_parse_progress(
    *,
    governor_decision: str,
    requested_documents: list[str],
) -> None:
    assert governor_decision in {
        "continue_interview",
        "need_more_evidence",
        "high_risk_review",
    }
    normalized_documents = [
        document_type.lower().replace("-", "_") for document_type in requested_documents
    ]
    assert "funding_proof" not in normalized_documents


def assert_openai_compat_metadata(
    metadata: dict,
    *,
    session_id: str,
    context_mode: str,
    governor_decision: str | None = None,
    requested_documents: list[str] | None = None,
) -> None:
    assert metadata["session_id"] == session_id
    assert metadata["phase_state"] == "interview"
    assert metadata["context_mode"] == context_mode
    if governor_decision is not None:
        assert metadata["governor_decision"] == governor_decision
    if requested_documents is not None:
        assert metadata["requested_documents"] == requested_documents
    assert isinstance(metadata["turn_decision"], dict)
    assert isinstance(metadata["prompt_trace"], dict)


@pytest.mark.live_llm
def test_live_openai_compat_maps_to_domain_flow(
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
    session_id = payload["metadata"]["session_id"]
    assert session_id.startswith("sess-")
    assert_openai_compat_metadata(
        payload["metadata"],
        session_id=session_id,
        context_mode="new_session",
        governor_decision="need_more_evidence",
        requested_documents=payload["metadata"]["requested_documents"],
    )
    assert build_calls == [
        (
            "adjudication_agent",
            "interview_turn",
            live_expected_runtime_model("adjudication_agent", "interview_turn"),
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
    assert_openai_compat_metadata(
        first_payload["metadata"],
        session_id=session_id,
        context_mode="new_session",
        governor_decision="need_more_evidence",
        requested_documents=first_payload["metadata"]["requested_documents"],
    )

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
    assert_openai_compat_metadata(
        second_payload["metadata"],
        session_id=session_id,
        context_mode="existing_session",
    )
    assert second_payload["metadata"]["governor_decision"] in {
        "continue_interview",
        "need_more_evidence",
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
    assert_openai_compat_metadata(
        third_payload["metadata"],
        session_id=session_id,
        context_mode="existing_session",
        governor_decision=third_payload["metadata"]["governor_decision"],
        requested_documents=third_payload["metadata"]["requested_documents"],
    )
    assert_live_post_parse_progress(
        governor_decision=third_payload["metadata"]["governor_decision"],
        requested_documents=third_payload["metadata"]["requested_documents"],
    )
    assert third_payload["choices"][0]["message"]["role"] == "assistant"
    assert third_payload["choices"][0]["message"]["content"]

    user_report_response = live_api_client.get(f"/v1/sessions/{session_id}/reports/user")

    assert user_report_response.status_code == 200
    user_report = user_report_response.json()
    assert user_report["turn_decision"]["decision"] == third_payload["metadata"][
        "turn_decision"
    ]["decision"]
    assert user_report["interview_status"] in {"verify_key_issue", "waiting_key_proof"}
    if user_report["current_key_question"] is not None:
        assert user_report["current_key_question"] == third_payload["choices"][0][
            "message"
        ]["content"]
    else:
        assert user_report["current_key_proof"] is not None
        assert user_report["current_key_proof"].lower().replace("-", "_") != "funding_proof"
    assert build_calls
    assert build_calls[-1] == (
        "adjudication_agent",
        "interview_turn",
        live_expected_runtime_model("adjudication_agent", "interview_turn"),
    )
    assert run_calls
    assert run_calls[-1] == session_id
    assert set(run_calls) == {session_id}
    with live_db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
    assert session_count == 1
