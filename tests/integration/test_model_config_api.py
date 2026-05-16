from fastapi.testclient import TestClient
import httpx
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
    requested: dict[str, str] = {}
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", True)

    def fake_get(self, url: str, *, headers: dict[str, str]):
        requested["url"] = url
        requested["authorization"] = headers["Authorization"]
        return httpx.Response(
            200,
            request=httpx.Request("GET", url),
            json={
                "object": "list",
                "data": [
                    {"id": "gpt-4.1-mini"},
                    {"id": "gpt-4.1"},
                    {"id": "gpt-4.1-mini"},
                ],
            },
        )

    monkeypatch.setattr(httpx.Client, "get", fake_get)

    response = client.post(
        "/v1/model-config/models",
        json={
            "base_url": "https://models.example.test",
            "api_key": "user-key",
        },
    )

    assert response.status_code == 200
    assert requested == {
        "url": "https://models.example.test/v1/models",
        "authorization": "Bearer user-key",
    }
    assert response.json() == {
        "models": [
            {"id": "gpt-4.1-mini", "label": "gpt-4.1-mini"},
            {"id": "gpt-4.1", "label": "gpt-4.1"},
        ]
    }
