from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def client(tmp_path) -> Generator[TestClient, None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'messages-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    def override_get_db() -> Generator[Session, None, None]:
        db = testing_session_local()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


def test_message_turn_returns_next_question_and_governor_decision(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "need_more_evidence"
    assert payload["assistant_message"] == "Please upload funding proof."


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
) -> None:
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
    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert upload_response.status_code == 202
    assert response.status_code == 200
    payload = response.json()
    assert payload["governor_decision"] == "continue_interview"
    assert payload["assistant_message"] == "What is the purpose of your travel?"
    assert payload["requested_documents"] == []


def test_confirmed_fraud_message_triggers_simulated_refusal(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

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
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

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
