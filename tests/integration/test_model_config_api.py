from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import AdminSettingRecord
from app.db.session import get_db
from app.main import app


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'model-config-api.sqlite3'}",
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


def enable_admin_user_model_config(db_session_factory) -> None:
    with db_session_factory() as db:
        db.merge(
            AdminSettingRecord(
                setting_key="demo",
                value_json={"user_model_config_enabled": True},
            )
        )
        db.commit()


def test_model_list_rejects_when_user_model_config_disabled(client: TestClient) -> None:
    response = client.post(
        "/v1/model-config/models",
        json={
            "base_url": "https://models.example.test/v1",
            "api_key": "user-key",
        },
    )

    assert response.status_code == 403
    assert "未启用用户自定义模型配置" in response.json()["detail"]


def test_model_list_proxies_openai_compatible_models(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}
    enable_admin_user_model_config(db_session_factory)

    class FakeModelListResponse:
        def model_dump(self, *, mode: str):
            requested["mode"] = mode
            return {
                "object": "list",
                "data": [
                    {"id": "gpt-4.1-mini"},
                    {"id": "gpt-4.1"},
                    {"id": "gpt-4.1-mini"},
                ],
            }

    class FakeModels:
        def list(self):
            requested["list_called"] = True
            return FakeModelListResponse()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            requested["kwargs"] = kwargs
            self.models = FakeModels()

    monkeypatch.setattr("app.api.routers.model_config.OpenAI", FakeOpenAI)

    response = client.post(
        "/v1/model-config/models",
        json={
            "base_url": "https://models.example.test",
            "api_key": "user-key",
        },
    )

    assert response.status_code == 200
    assert requested["mode"] == "json"
    assert requested["list_called"] is True
    assert requested["kwargs"] == {
        "api_key": "user-key",
        "base_url": "https://models.example.test/v1",
        "timeout": settings_module.settings.openai_timeout_seconds,
        "max_retries": 0,
        "default_headers": {"User-Agent": "curl/8.5.0"},
    }
    assert response.json() == {
        "models": [
            {"id": "gpt-4.1-mini", "label": "gpt-4.1-mini"},
            {"id": "gpt-4.1", "label": "gpt-4.1"},
        ]
    }
