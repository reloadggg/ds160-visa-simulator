import os

import pytest

from app.core import settings as settings_module
from app.services.admin_config_service import EffectiveModelConfig, admin_model_runtime


def _allows_db_admin_model_config(request: pytest.FixtureRequest) -> bool:
    test_path = request.node.path.as_posix()
    return test_path.endswith("tests/integration/test_admin_demo_api.py")


@pytest.fixture(autouse=True)
def disable_multimodal_extraction_by_default(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
):
    is_live_llm_test = request.node.get_closest_marker("live_llm") is not None
    isolate_admin_model_config = (
        not is_live_llm_test and not _allows_db_admin_model_config(request)
    )
    monkeypatch.setenv("MULTIMODAL_EXTRACTION_ENABLED", "false")
    monkeypatch.setenv("RAG_ENABLED", "false")
    if not is_live_llm_test:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
        for key in list(os.environ):
            if key.startswith("RUNTIME_"):
                monkeypatch.delenv(key, raising=False)
        monkeypatch.setattr(settings_module.settings, "openai_api_key", None)
        monkeypatch.setattr(settings_module.settings, "openai_base_url", None)
    monkeypatch.delenv("SILICONFLOW_API_KEY", raising=False)
    monkeypatch.delenv("APP_AUTH_PASSWORD", raising=False)
    monkeypatch.setattr(settings_module.settings, "app_auth_password", None)
    monkeypatch.setattr(settings_module.settings, "allow_debug_fill", True)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_config", False)
    monkeypatch.setattr(settings_module.settings, "allow_user_model_streaming", False)
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "native_interviewer")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_canary_percent", 0)
    monkeypatch.setattr(settings_module.settings, "agent_runtime_trace_enabled", True)
    monkeypatch.setattr(
        settings_module.settings,
        "agent_runtime_typed_adjudication_enabled",
        False,
    )
    monkeypatch.setattr(settings_module.settings, "rag_enabled", False)
    monkeypatch.setattr(settings_module.settings, "siliconflow_api_key", None)
    if isolate_admin_model_config:
        # Non-live tests must not inherit the developer machine's persisted
        # admin model settings from the default app database.  Keep env fallback
        # behavior intact: tests that set OPENAI_* still work because model
        # resolution falls through to os.environ when this runtime config is
        # empty and source="env".
        with admin_model_runtime(
            EffectiveModelConfig(
                base_url=None,
                api_key=None,
                model=None,
                streaming_enabled=True,
                source="env",
            )
        ):
            yield
        return
    yield
