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
    choice = response.json()["choices"][0]["message"]
    assert choice["role"] == "assistant"
    assert choice["content"] == "Please upload funding proof."


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
