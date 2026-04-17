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
        f"sqlite:///{tmp_path / 'sessions-api.sqlite3'}",
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


def test_create_session_returns_initial_phase(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert response.status_code == 201
    payload = response.json()
    assert payload["phase_state"] == "intake"
    assert payload["current_governor_decision"] == "need_more_evidence"


def test_required_package_endpoint_uses_declared_family(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 200
    assert response.json()["required_initial_package"] == [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
    ]
