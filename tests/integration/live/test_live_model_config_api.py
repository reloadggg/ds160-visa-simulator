import os

import pytest


@pytest.mark.live_llm
def test_live_model_config_uses_openai_sdk_models_list(live_api_client, monkeypatch):
    monkeypatch.setattr("app.core.settings.settings.allow_user_model_config", True)
    response = live_api_client.post(
        "/v1/model-config/models",
        json={
            "base_url": os.environ["OPENAI_BASE_URL"],
            "api_key": os.environ["OPENAI_API_KEY"],
        },
    )

    assert response.status_code == 200
    models = response.json()["models"]
    assert models
    assert all(model["id"] and model["label"] for model in models)
