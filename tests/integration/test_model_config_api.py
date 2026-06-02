from fastapi.testclient import TestClient
import pytest

from app.core import settings as settings_module
from app.main import app


@pytest.fixture()
def client() -> TestClient:
    with TestClient(app) as test_client:
        return test_client


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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requested: dict[str, object] = {}
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", True)

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
