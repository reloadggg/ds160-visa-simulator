from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from pydantic_ai.models.test import TestModel
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app
from app.workers.parse_worker import ParseWorker


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'messages-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session_factory) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def prepare_ready_for_interview_session(
    client: TestClient,
    db_session_factory,
) -> str:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]
    assert session_resp.status_code == 201
    for filename, raw_bytes in [
        ("ds160.txt", b"Completed DS-160 form draft"),
        ("passport_bio.txt", b"Passport biographic page"),
        ("i20.txt", b"Form I-20 issued by school"),
        ("admission_letter.txt", b"University admission letter"),
        ("funding_proof.txt", b"Parent sponsor bank statement for tuition"),
    ]:
        upload_response = client.post(
            f"/v1/sessions/{session_id}/files",
            files={"file": (filename, raw_bytes, "text/plain")},
        )
        assert upload_response.status_code == 202

    with db_session_factory() as db:
        while ParseWorker(db).run_once():
            pass

    return session_id


def test_message_turn_short_circuits_to_gate_when_gate_not_ready(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.ExtractorService.apply_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("gate 未通过前不应进入正式 interview runtime")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert (
        payload["assistant_message"]
        == "Please upload the required documents before continuing."
    )

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "gate_review"
        assert record.gate_status_json["status"] == "pending_documents"


def test_message_turn_uses_question_agent_output_for_continue_interview(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            TestModel(
                call_tools=[],
                custom_output_args={
                    "assistant_message": "What is the purpose of your travel?",
                    "requested_documents": [],
                    "decision_hint": "continue_interview",
                },
            ),
            {"model": "gpt-5.4"},
        )
        if module_key == "question_agent"
        else (None, {"model": None}),
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService._fallback_question_action",
        lambda self, governor_decision, score: (_ for _ in ()).throw(
            AssertionError("question agent path should not fall back")
        ),
    )

    session_id = prepare_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["requested_documents"] == []

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert [entry["node_name"] for entry in record.runtime_trace_json] == [
            "receive_input",
            "extract_claims",
            "resolve_evidence",
            "consistency_check",
            "score_case",
            "governor_decide",
            "build_next_action",
        ]
        assert record.score_history_json[-1]["scoring_stage"] == "interview_turn"
        assert record.governor_history_json[-1]["decision"] == "continue_interview"


def test_message_turn_falls_back_when_question_agent_errors(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.AgentModelFactory.build",
        lambda self, module_key, stage_key: (
            object(),
            {"model": "gpt-5.4"},
        )
        if module_key == "question_agent"
        else (None, {"model": None}),
    )
    monkeypatch.setattr(
        "app.services.interview_runtime_service.QuestionAgentRunner.run",
        lambda self, **kwargs: (_ for _ in ()).throw(RuntimeError("runtime failure")),
    )

    session_id = prepare_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["requested_documents"] == []


def test_message_turn_rejects_non_user_role(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "assistant", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 422


def test_funding_proof_upload_allows_interview_to_continue(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.ExtractorService.apply_message",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("worker 完成前不应进入正式 interview runtime")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )
    pre_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert pre_worker_response.status_code == 200
    assert pre_worker_response.json()["governor_decision"] == "need_more_evidence"
    assert (
        pre_worker_response.json()["assistant_message"]
        == "Your uploaded documents are waiting to be parsed."
    )

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    monkeypatch.undo()

    post_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert post_worker_response.status_code == 200
    payload = post_worker_response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert (
        payload["assistant_message"]
        == "Please upload the required documents before continuing."
    )
    assert sorted(payload["requested_documents"]) == sorted(
        ["ds160", "passport_bio", "i20", "admission_letter"]
    )


def test_confirmed_fraud_message_triggers_simulated_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = prepare_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I lied on my DS-160 and used fake bank statements.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "simulated_refusal"


def test_negated_fraud_statement_does_not_trigger_refusal(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = prepare_ready_for_interview_session(client, db_session_factory)

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "I did not use fake bank statements.",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
