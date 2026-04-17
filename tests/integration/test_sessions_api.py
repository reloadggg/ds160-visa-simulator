from fastapi.testclient import TestClient

from app.main import app


def test_create_session_returns_initial_phase() -> None:
    client = TestClient(app)

    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["phase_state"] == "intake"
    assert payload["current_governor_decision"] == "need_more_evidence"
