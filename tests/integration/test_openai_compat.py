from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'openai-compat.sqlite3'}",
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


def test_chat_completions_maps_to_domain_flow(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay for my studies."}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 200
    payload = response.json()
    choice = payload["choices"][0]["message"]
    assert choice["role"] == "assistant"
    assert choice["content"]
    assert set(payload["metadata"]) == {
        "session_id",
        "phase_state",
        "context_mode",
    }
    assert payload["metadata"]["session_id"].startswith("sess-")
    assert payload["metadata"]["phase_state"] == "gate_review"
    assert payload["metadata"]["context_mode"] == "new_session"


def test_chat_completions_uses_same_runtime_gate_initialization(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "I am funded by my institution."}],
            "metadata": {"declared_family": "j1"},
        },
    )

    assert response.status_code == 200
    session_id = response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)

    assert record is not None
    assert record.gate_status_json["scenario_key"] == "institution_funded"
    assert [doc["document_type"] for doc in record.gate_status_json["required_documents"]] == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
    ]


def test_chat_completions_reuses_existing_session_when_metadata_session_id_present(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    handled_session_ids: list[str] = []

    def fake_handle_user_turn(self, session_id: str, message_text: str) -> dict:
        handled_session_ids.append(session_id)
        return {
            "assistant_message": f"handled: {message_text}",
            "governor_decision": "continue_interview",
            "score_summary": {
                "category_fit": 60,
                "document_readiness": 50,
                "narrative_consistency": 55,
                "confidence": 58,
            },
            "requested_documents": [],
            "gate_progress": {
                "overall_status": "ready_for_interview",
                "ready_count": 0,
                "uploaded_count": 0,
                "missing_count": 0,
                "documents": [],
            },
        }

    monkeypatch.setattr(
        "app.services.message_service.MessageService.handle_user_turn",
        fake_handle_user_turn,
    )

    first_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "First turn"}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert first_response.status_code == 200
    first_session_id = first_response.json()["metadata"]["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, first_session_id)
        assert record is not None
        record.phase_state = "interview"
        db.add(record)
        db.commit()

    second_response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {"role": "assistant", "content": "Previous reply"},
                {"role": "user", "content": "Second turn"},
            ],
            "metadata": {
                "session_id": first_session_id,
                "declared_family": "j1",
            },
        },
    )

    assert second_response.status_code == 200
    second_payload = second_response.json()
    assert handled_session_ids == [first_session_id, first_session_id]
    assert second_payload["choices"][0]["message"]["content"] == "handled: Second turn"
    assert second_payload["metadata"] == {
        "session_id": first_session_id,
        "phase_state": "interview",
        "context_mode": "existing_session",
    }

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 1


def test_chat_completions_returns_404_for_unknown_metadata_session_id(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "Resume missing session"}],
            "metadata": {"session_id": "sess-missing"},
        },
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: sess-missing"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0


def test_chat_completions_rejects_empty_messages(client: TestClient) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 422


def test_chat_completions_rejects_missing_user_message_without_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "system", "content": "hi"}],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "at least one user message is required"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0


def test_chat_completions_rejects_unsupported_family_without_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [{"role": "user", "content": "My parents will pay for my studies."}],
            "metadata": {"declared_family": "zzz"},
        },
    )

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported declared_family: zzz"

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))
        assert session_count == 0
