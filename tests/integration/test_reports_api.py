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
        f"sqlite:///{tmp_path / 'reports-api.sqlite3'}",
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


def test_user_report_returns_summary_shape(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    assert "outcome_label" in response.json()


def test_user_report_stays_consistent_after_funding_proof(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )
    client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )
    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert response.status_code == 200
    payload = response.json()
    assert payload["outcome_label"] == "可继续正式问答"
    assert payload["missing_evidence"] == []
    assert payload["recommended_improvements"] == [
        "继续回答后续问题，并保持叙事一致。",
    ]
