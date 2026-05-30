import pytest

from app.core import settings as settings_module


@pytest.fixture(autouse=True)
def disable_multimodal_extraction_by_default(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    is_live_llm_test = request.node.get_closest_marker("live_llm") is not None
    monkeypatch.setenv("MULTIMODAL_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    if not is_live_llm_test:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        monkeypatch.setattr(settings_module.settings, "openai_api_key", None)
        monkeypatch.setattr(settings_module.settings, "openai_base_url", None)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    monkeypatch.setattr(settings_module.settings, "app_auth_password", None)
    monkeypatch.setattr(settings_module.settings, "allow_debug_fill", True)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", False)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", False)
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "legacy")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 0)
    monkeypatch.setattr(settings_module.settings, "agent_runtime_trace_enabled", True)
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", False)
    monkeypatch.setattr(settings_module.settings, "agent_runtime_typed_adjudication_enabled", False)
    monkeypatch.setattr(settings_module.settings, "rag_enabled", False)
    monkeypatch.setattr(settings_module.settings, "siliconflow_api_key", None)
