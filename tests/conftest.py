import pytest

from app.core import settings as settings_module


@pytest.fixture(autouse=True)
def disable_multimodal_extraction_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MULTIMODAL_EXTRACTION_ENABLED", "false")
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    monkeypatch.setattr(settings_module.settings, "app_auth_password", None)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", False)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", False)
