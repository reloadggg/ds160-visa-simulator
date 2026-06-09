from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Iterator

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.models import AdminSettingRecord
from app.agents.user_model_config import normalize_openai_base_url

DEMO_SETTINGS_KEY = "demo"

DEFAULT_DEMO_SETTINGS: dict[str, Any] = {
    "model_base_url": None,
    "model_api_key": None,
    "model_name": None,
    "model_streaming_enabled": True,
    "user_model_config_enabled": False,
    "show_github_link": False,
    "wx_entry_enabled": False,
    "debug_console_enabled": False,
    "debug_material_enabled": False,
    "rag_status_user_visible": False,
}


@dataclass(frozen=True)
class EffectiveModelConfig:
    base_url: str | None
    api_key: str | None
    model: str | None
    streaming_enabled: bool
    source: str


_admin_model_runtime_config: ContextVar[EffectiveModelConfig | None] = ContextVar(
    "admin_model_runtime_config",
    default=None,
)


@contextmanager
def admin_model_runtime(config: EffectiveModelConfig | None) -> Iterator[None]:
    token = _admin_model_runtime_config.set(config)
    try:
        yield
    finally:
        _admin_model_runtime_config.reset(token)


def current_admin_model_runtime_config() -> EffectiveModelConfig | None:
    return _admin_model_runtime_config.get()


class AdminConfigService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_settings(self) -> dict[str, Any]:
        record = self.db.get(AdminSettingRecord, DEMO_SETTINGS_KEY)
        payload = _default_demo_settings()
        if record is not None and isinstance(record.value_json, dict):
            payload.update(record.value_json)
        return payload

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        for key in DEFAULT_DEMO_SETTINGS:
            if key not in patch:
                continue
            value = patch[key]
            if key == "model_api_key":
                cleaned_key = _clean_string(value)
                if cleaned_key is None:
                    continue
                current[key] = cleaned_key
                continue
            if key == "model_base_url":
                cleaned_base_url = _clean_string(value)
                current[key] = (
                    normalize_openai_base_url(cleaned_base_url)
                    if cleaned_base_url is not None
                    else None
                )
                continue
            if key == "model_name":
                current[key] = _clean_string(value)
                continue
            current[key] = value
        record = self.db.get(AdminSettingRecord, DEMO_SETTINGS_KEY)
        if record is None:
            record = AdminSettingRecord(setting_key=DEMO_SETTINGS_KEY, value_json=current)
        else:
            record.value_json = current
        self.db.add(record)
        self.db.commit()
        return current

    def public_app_config(self) -> dict[str, Any]:
        current = self.get_settings()
        return {
            "show_github_link": bool(current.get("show_github_link")),
            "wx_entry_enabled": bool(current.get("wx_entry_enabled")),
            "debug_console_enabled": bool(current.get("debug_console_enabled")),
            "debug_material_enabled": bool(
                current.get("debug_console_enabled")
                and current.get("debug_material_enabled")
            ),
            # Product rule: user-side BYOK is not part of normal operation.
            # The admin DB flag can still guard legacy/internal endpoints, but
            # public app config must not advertise user model controls.
            "user_model_config_enabled": False,
            # Product rule for the online demo: RAG/knowledge-base status is an
            # admin-only operational surface. Keep the field for frontend
            # compatibility, but never expose it to the user workbench.
            "rag_status_user_visible": False,
        }

    def admin_payload(self) -> dict[str, Any]:
        current = self.get_settings()
        masked = dict(current)
        masked.pop("model_api_key", None)
        masked["model_api_key_configured"] = bool(current.get("model_api_key"))
        return masked

    def effective_model_config(self) -> EffectiveModelConfig:
        runtime_config = current_admin_model_runtime_config()
        if runtime_config is not None:
            return runtime_config

        current = self.get_settings()
        base_url = _clean_string(current.get("model_base_url"))
        api_key = _clean_string(current.get("model_api_key"))
        model = _clean_string(current.get("model_name"))
        if base_url and api_key and model:
            return EffectiveModelConfig(
                base_url=base_url,
                api_key=api_key,
                model=model,
                streaming_enabled=bool(current.get("model_streaming_enabled", True)),
                source="admin",
            )
        return EffectiveModelConfig(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=None,
            streaming_enabled=True,
            source="env",
        )

    def user_model_config_enabled(self) -> bool:
        return bool(self.get_settings().get("user_model_config_enabled"))

    def debug_console_enabled(self) -> bool:
        return bool(self.get_settings().get("debug_console_enabled"))

    def debug_material_enabled(self) -> bool:
        current = self.get_settings()
        return bool(current.get("debug_console_enabled") and current.get("debug_material_enabled"))


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _default_demo_settings() -> dict[str, Any]:
    payload = dict(DEFAULT_DEMO_SETTINGS)
    # Preserve existing env-driven local/test behavior when no admin setting has
    # been saved yet, while production remains closed by default because these
    # settings default to false.
    payload["debug_console_enabled"] = bool(
        settings.allow_runtime_debug or settings.allow_debug_fill
    )
    payload["debug_material_enabled"] = bool(settings.allow_debug_fill)
    return payload
