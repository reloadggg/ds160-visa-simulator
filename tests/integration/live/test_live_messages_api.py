import pytest

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.workers.parse_worker import ParseWorker


@pytest.mark.live_llm
def test_live_messages_api_requests_funding_proof(
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
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert payload["assistant_message"]
    assert (
        payload["requested_documents"]
        or "upload" in payload["assistant_message"].lower()
        or "evidence" in payload["assistant_message"].lower()
    )
    assert build_calls == [
        (
            "question_agent",
            "interview_turn",
            live_expected_runtime_model("question_agent", "interview_turn"),
        )
    ]
    assert run_calls == [session_id]


@pytest.mark.live_llm
def test_live_messages_api_continues_after_funding_document_upload(
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
    session_resp = live_api_client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
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
    pre_worker = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )
    with live_db_session_factory() as db:
        processed_any = False
        while ParseWorker(db).run_once():
            processed_any = True
    assert processed_any is True
    response = live_api_client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert pre_worker.status_code == 200
    assert pre_worker.json()["governor_decision"] == "need_more_evidence"
    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"]
    assert payload["requested_documents"] == []
    assert build_calls
    assert build_calls[-1] == (
        "question_agent",
        "interview_turn",
        live_expected_runtime_model("question_agent", "interview_turn"),
    )
    assert run_calls[-1] == session_id
