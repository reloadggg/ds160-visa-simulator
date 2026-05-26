from collections.abc import Generator
from types import SimpleNamespace

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionTurnRecord
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'openai-responses.sqlite3'}",
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


def test_responses_create_maps_to_domain_flow(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: SimpleNamespace(
            assistant_message="Please explain your funding plan.",
            requested_documents=[],
            decision_hint="continue_interview",
        ),
    )

    response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "My parents will pay for my studies.",
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["object"] == "response"
    assert payload["status"] == "completed"
    assert payload["id"].startswith("resp-sess-")
    assert payload["output_text"]
    assert payload["output"][0]["content"][0]["type"] == "output_text"
    assert payload["metadata"]["session_id"].startswith("sess-")
    assert payload["metadata"]["context_mode"] == "new_session"
    assert isinstance(payload["metadata"]["runtime_view_state"], dict)


def test_responses_previous_response_id_reuses_local_session_transcript(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handled_messages: list[str] = []

    def fake_run_turn(self, record, message_text):
        handled_messages.append(message_text)
        return {
            "assistant_message": f"next: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"handled_messages": list(handled_messages)},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    first_response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "I will study data science at Example University.",
            "metadata": {"declared_family": "f1"},
        },
    )
    first_payload = first_response.json()
    session_id = first_payload["metadata"]["session_id"]
    previous_response_id = first_payload["id"]

    second_response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "previous_response_id": previous_response_id,
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": "My parents will fund the first year.",
                        }
                    ],
                }
            ],
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert second_response.json()["metadata"]["session_id"] == session_id
    assert second_response.json()["metadata"]["context_mode"] == "previous_response"
    assert handled_messages == [
        "I will study data science at Example University.",
        "My parents will fund the first year.",
    ]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [turn.role for turn in turns] == [
        "user",
        "assistant",
        "user",
        "assistant",
    ]


def test_responses_derives_idempotency_key_without_explicit_metadata_key(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled #{run_count}: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )

    first_response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "My parents will fund the first year.",
            "metadata": {"declared_family": "f1"},
        },
    )
    session_id = first_response.json()["metadata"]["session_id"]
    second_response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "My parents will fund the first year.",
            "metadata": {"session_id": session_id},
        },
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["id"] == first_response.json()["id"]

    with db_session_factory() as db:
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]


def test_responses_supports_http_idempotency_key_for_new_session_replay(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    run_count = 0

    def fake_run_turn(self, record, message_text):
        nonlocal run_count
        run_count += 1
        return {
            "assistant_message": f"handled once: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {"run_count": run_count},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        fake_run_turn,
    )
    request_body = {
        "model": "visa-simulator-v1",
        "input": "This request may be retried.",
        "metadata": {"declared_family": "f1"},
    }

    first_response = client.post(
        "/v1/responses",
        json=request_body,
        headers={"Idempotency-Key": "responses-new-session-retry"},
    )
    second_response = client.post(
        "/v1/responses",
        json=request_body,
        headers={"Idempotency-Key": "responses-new-session-retry"},
    )

    assert first_response.status_code == 200
    assert second_response.status_code == 200
    assert run_count == 1
    assert second_response.json()["id"] == first_response.json()["id"]
    assert second_response.json()["metadata"]["context_mode"] == "idempotency_replay"

    with db_session_factory() as db:
        turns = db.scalars(select(SessionTurnRecord)).all()

    assert [turn.role for turn in turns] == ["user", "assistant"]


def test_responses_rejects_mismatched_previous_response_session(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "previous_response_id": "resp-sess-one-2",
            "input": "Continue.",
            "metadata": {"session_id": "sess-two"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == (
        "metadata.session_id does not match previous_response_id"
    )


def test_responses_rejects_previous_response_id_without_matching_assistant_turn(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.run_turn",
        lambda self, record, message_text: {
            "assistant_message": "handled",
            "governor_decision": "continue_interview",
            "score_summary": {},
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "prompt_trace": {},
        },
    )
    first_response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "input": "Start.",
            "metadata": {"declared_family": "f1"},
        },
    )
    session_id = first_response.json()["metadata"]["session_id"]

    response = client.post(
        "/v1/responses",
        json={
            "model": "visa-simulator-v1",
            "previous_response_id": f"resp-{session_id}-999",
            "input": "Continue.",
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == (
        f"previous_response_id not found: resp-{session_id}-999"
    )
