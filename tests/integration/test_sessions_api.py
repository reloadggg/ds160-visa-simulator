from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'sessions-api.sqlite3'}",
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


def test_required_package_rejects_unlocked_family(client: TestClient) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": None})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/required-package")

    assert response.status_code == 409
    assert response.json()["detail"] == "declared_family not locked"


def test_create_session_rejects_unsupported_family(client: TestClient) -> None:
    response = client.post("/v1/sessions", json={"declared_family": "zzz"})

    assert response.status_code == 422
    assert response.json()["detail"] == "unsupported declared_family: zzz"


def test_required_package_rejects_invalid_stored_family(
    client: TestClient,
    db_session_factory,
) -> None:
    with db_session_factory() as db:
        db.add(SessionRecord(session_id="sess-bad", declared_family="zzz"))
        db.commit()

    response = client.get("/v1/sessions/sess-bad/required-package")

    assert response.status_code == 409
    assert response.json()["detail"] == "unsupported declared_family: zzz"
