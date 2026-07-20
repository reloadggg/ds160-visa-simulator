from __future__ import annotations

import secrets
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime
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
    # Multi-channel model providers (admin-managed). Active channel drives
    # runtime and is mirrored into the legacy flat fields above.
    "model_channels": [],
    "active_model_channel_id": None,
    "user_model_config_enabled": False,
    "show_github_link": False,
    "wx_entry_enabled": False,
    "debug_console_enabled": False,
    "debug_material_enabled": False,
    # Product feature (default ON): AI practice materials from user seed text.
    # Not a debug tool — independent of debug_console / debug_material.
    "practice_materials_enabled": True,
    "rag_status_user_visible": False,
}


@dataclass(frozen=True)
class EffectiveModelConfig:
    base_url: str | None
    api_key: str | None
    model: str | None
    streaming_enabled: bool
    source: str
    channel_id: str | None = None
    channel_name: str | None = None


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
        stored: dict[str, Any] = {}
        if record is not None and isinstance(record.value_json, dict):
            stored = dict(record.value_json)
            payload.update(stored)
        # Migration: product feature defaults ON when never stored in admin JSON.
        if "practice_materials_enabled" not in stored:
            payload["practice_materials_enabled"] = True
        # Ensure multi-channel shape exists and mirrors legacy single config.
        # Persist when we invent channels from legacy flat fields so channel ids
        # stay stable across subsequent get_settings / effective_model_config.
        needs_channel_persist = _needs_channel_migration_persist(stored, payload)
        payload = _ensure_channel_shape(payload)
        if needs_channel_persist:
            self._persist(payload)
        return payload

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        current = self.get_settings()
        legacy_model_keys = {
            "model_base_url",
            "model_api_key",
            "model_name",
            "model_streaming_enabled",
        }
        touches_legacy_model = any(key in patch for key in legacy_model_keys)

        for key in DEFAULT_DEMO_SETTINGS:
            if key not in patch:
                continue
            if key in {"model_channels", "active_model_channel_id"}:
                # Channel list mutations go through dedicated methods.
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

        if touches_legacy_model:
            current = _upsert_active_from_legacy(current)

        current = _ensure_channel_shape(current)
        self._persist(current)
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
            "practice_materials_enabled": bool(
                current.get("practice_materials_enabled", True)
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
        channels = _channels_list(current)
        masked["model_channels"] = [
            _public_channel(channel) for channel in channels
        ]
        masked["active_model_channel_id"] = current.get("active_model_channel_id")
        return masked

    def effective_model_config(self) -> EffectiveModelConfig:
        runtime_config = current_admin_model_runtime_config()
        if runtime_config is not None:
            return runtime_config

        current = self.get_settings()
        active = _active_channel(current)
        if active is not None:
            base_url = _clean_string(active.get("base_url"))
            api_key = _clean_string(active.get("api_key"))
            model = _clean_string(active.get("model"))
            if base_url and api_key and model:
                return EffectiveModelConfig(
                    base_url=base_url,
                    api_key=api_key,
                    model=model,
                    streaming_enabled=bool(
                        active.get(
                            "streaming_enabled",
                            current.get("model_streaming_enabled", True),
                        )
                    ),
                    source="admin",
                    channel_id=_clean_string(active.get("id")),
                    channel_name=_clean_string(active.get("name")),
                )

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
                channel_id=_clean_string(current.get("active_model_channel_id")),
                channel_name=None,
            )
        return EffectiveModelConfig(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            model=None,
            streaming_enabled=True,
            source="env",
            channel_id=None,
            channel_name=None,
        )

    def list_model_channels(self) -> list[dict[str, Any]]:
        current = self.get_settings()
        return [_public_channel(channel) for channel in _channels_list(current)]

    def create_model_channel(
        self,
        *,
        name: str,
        base_url: str,
        api_key: str,
        model: str | None = None,
        streaming_enabled: bool = True,
        activate: bool = False,
    ) -> dict[str, Any]:
        cleaned_name = _clean_string(name)
        cleaned_base = _clean_string(base_url)
        cleaned_key = _clean_string(api_key)
        cleaned_model = _clean_string(model)
        if not cleaned_name:
            raise ValueError("channel name is required")
        if not cleaned_base:
            raise ValueError("channel base_url is required")
        if not cleaned_key:
            raise ValueError("channel api_key is required")

        current = self.get_settings()
        channels = _channels_list(current)
        now = _utc_now_iso()
        channel = {
            "id": _new_channel_id(),
            "name": cleaned_name,
            "base_url": normalize_openai_base_url(cleaned_base),
            "api_key": cleaned_key,
            "model": cleaned_model,
            "streaming_enabled": bool(streaming_enabled),
            "created_at": now,
            "updated_at": now,
        }
        channels.append(channel)
        current["model_channels"] = channels
        if activate or not current.get("active_model_channel_id"):
            current["active_model_channel_id"] = channel["id"]
            current = _mirror_active_to_legacy(current)
        current = _ensure_channel_shape(current)
        self._persist(current)
        return _public_channel(channel)

    def update_model_channel(
        self,
        channel_id: str,
        *,
        name: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        streaming_enabled: bool | None = None,
    ) -> dict[str, Any]:
        current = self.get_settings()
        channels = _channels_list(current)
        index = _channel_index(channels, channel_id)
        if index is None:
            raise LookupError(f"model channel not found: {channel_id}")

        channel = dict(channels[index])
        if name is not None:
            cleaned_name = _clean_string(name)
            if not cleaned_name:
                raise ValueError("channel name is required")
            channel["name"] = cleaned_name
        if base_url is not None:
            cleaned_base = _clean_string(base_url)
            if not cleaned_base:
                raise ValueError("channel base_url is required")
            channel["base_url"] = normalize_openai_base_url(cleaned_base)
        if api_key is not None:
            cleaned_key = _clean_string(api_key)
            if cleaned_key is not None:
                channel["api_key"] = cleaned_key
        if model is not None:
            channel["model"] = _clean_string(model)
        if streaming_enabled is not None:
            channel["streaming_enabled"] = bool(streaming_enabled)
        channel["updated_at"] = _utc_now_iso()
        channels[index] = channel
        current["model_channels"] = channels
        if current.get("active_model_channel_id") == channel["id"]:
            current = _mirror_active_to_legacy(current)
        current = _ensure_channel_shape(current)
        self._persist(current)
        return _public_channel(channel)

    def delete_model_channel(self, channel_id: str) -> dict[str, Any]:
        current = self.get_settings()
        channels = _channels_list(current)
        index = _channel_index(channels, channel_id)
        if index is None:
            raise LookupError(f"model channel not found: {channel_id}")

        removed = channels.pop(index)
        current["model_channels"] = channels
        if current.get("active_model_channel_id") == removed.get("id"):
            next_active = channels[0]["id"] if channels else None
            current["active_model_channel_id"] = next_active
            current = _mirror_active_to_legacy(current)
        current = _ensure_channel_shape(current)
        self._persist(current)
        return {
            "deleted_channel_id": removed.get("id"),
            "active_model_channel_id": current.get("active_model_channel_id"),
            "model_channels": [
                _public_channel(channel) for channel in _channels_list(current)
            ],
        }

    def activate_model_channel(self, channel_id: str) -> dict[str, Any]:
        current = self.get_settings()
        channels = _channels_list(current)
        index = _channel_index(channels, channel_id)
        if index is None:
            raise LookupError(f"model channel not found: {channel_id}")
        current["active_model_channel_id"] = channels[index]["id"]
        current = _mirror_active_to_legacy(current)
        current = _ensure_channel_shape(current)
        self._persist(current)
        return {
            "active_model_channel_id": current.get("active_model_channel_id"),
            "model_channels": [
                _public_channel(channel) for channel in _channels_list(current)
            ],
            "model_base_url": current.get("model_base_url"),
            "model_name": current.get("model_name"),
            "model_streaming_enabled": current.get("model_streaming_enabled"),
            "model_api_key_configured": bool(current.get("model_api_key")),
        }

    def get_model_channel_secrets(self, channel_id: str) -> dict[str, Any] | None:
        """Internal: full channel including api_key for test/list draft fallback."""
        current = self.get_settings()
        for channel in _channels_list(current):
            if channel.get("id") == channel_id:
                return dict(channel)
        return None

    def user_model_config_enabled(self) -> bool:
        return bool(self.get_settings().get("user_model_config_enabled"))

    def debug_console_enabled(self) -> bool:
        return bool(self.get_settings().get("debug_console_enabled"))

    def debug_material_enabled(self) -> bool:
        current = self.get_settings()
        return bool(current.get("debug_console_enabled") and current.get("debug_material_enabled"))

    def practice_materials_enabled(self) -> bool:
        """User-facing practice material generation (product default: ON).

        Independent of debug console. Admin may set false to disable.
        Missing key in legacy DB rows is treated as enabled.
        """
        current = self.get_settings()
        if "practice_materials_enabled" not in current:
            return True
        return bool(current.get("practice_materials_enabled"))

    def material_generation_enabled(self) -> bool:
        """Union of practice OR debug material flags for internal tooling only.

        Product practice routes must call ``practice_materials_enabled()`` alone;
        do not use this helper to gate user-facing practice APIs.
        """
        return self.practice_materials_enabled() or self.debug_material_enabled()

    def _persist(self, current: dict[str, Any]) -> None:
        record = self.db.get(AdminSettingRecord, DEMO_SETTINGS_KEY)
        if record is None:
            record = AdminSettingRecord(setting_key=DEMO_SETTINGS_KEY, value_json=current)
        else:
            record.value_json = current
        self.db.add(record)
        self.db.commit()


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
    # Practice materials are a normal product feature (default on).
    payload["practice_materials_enabled"] = True
    return payload


def _utc_now_iso() -> str:
    return datetime.now(UTC).replace(tzinfo=None).isoformat()


def _new_channel_id() -> str:
    return f"ch_{secrets.token_hex(8)}"


def _channels_list(settings_payload: dict[str, Any]) -> list[dict[str, Any]]:
    raw = settings_payload.get("model_channels")
    if not isinstance(raw, list):
        return []
    channels: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        channel_id = _clean_string(item.get("id"))
        if not channel_id:
            continue
        channels.append(dict(item))
    return channels


def _channel_index(channels: list[dict[str, Any]], channel_id: str) -> int | None:
    for index, channel in enumerate(channels):
        if channel.get("id") == channel_id:
            return index
    return None


def _active_channel(settings_payload: dict[str, Any]) -> dict[str, Any] | None:
    channels = _channels_list(settings_payload)
    active_id = _clean_string(settings_payload.get("active_model_channel_id"))
    if active_id:
        for channel in channels:
            if channel.get("id") == active_id:
                return channel
    if channels:
        return channels[0]
    return None


def _public_channel(channel: dict[str, Any]) -> dict[str, Any]:
    api_key = _clean_string(channel.get("api_key"))
    return {
        "id": channel.get("id"),
        "name": channel.get("name"),
        "base_url": channel.get("base_url"),
        "model": channel.get("model"),
        "streaming_enabled": bool(channel.get("streaming_enabled", True)),
        "api_key_configured": bool(api_key),
        "created_at": channel.get("created_at"),
        "updated_at": channel.get("updated_at"),
    }


def _mirror_active_to_legacy(settings_payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(settings_payload)
    active = _active_channel(payload)
    if active is None:
        payload["model_base_url"] = None
        payload["model_api_key"] = None
        payload["model_name"] = None
        payload["model_streaming_enabled"] = True
        payload["active_model_channel_id"] = None
        return payload
    payload["active_model_channel_id"] = active.get("id")
    payload["model_base_url"] = active.get("base_url")
    payload["model_api_key"] = active.get("api_key")
    payload["model_name"] = active.get("model")
    payload["model_streaming_enabled"] = bool(active.get("streaming_enabled", True))
    return payload


def _upsert_active_from_legacy(settings_payload: dict[str, Any]) -> dict[str, Any]:
    """When PATCH still uses flat model_* fields, keep channels in sync."""
    payload = dict(settings_payload)
    channels = _channels_list(payload)
    base_url = _clean_string(payload.get("model_base_url"))
    api_key = _clean_string(payload.get("model_api_key"))
    model = _clean_string(payload.get("model_name"))
    streaming = bool(payload.get("model_streaming_enabled", True))
    now = _utc_now_iso()

    # Nothing usable — leave channels alone except shape normalize.
    if not (base_url and api_key):
        return payload

    active_id = _clean_string(payload.get("active_model_channel_id"))
    index = _channel_index(channels, active_id) if active_id else None
    if index is None and channels:
        index = 0
        active_id = channels[0].get("id")

    if index is not None:
        channel = dict(channels[index])
        channel["base_url"] = (
            normalize_openai_base_url(base_url) if base_url else channel.get("base_url")
        )
        if api_key:
            channel["api_key"] = api_key
        channel["model"] = model
        channel["streaming_enabled"] = streaming
        channel["updated_at"] = now
        if not _clean_string(channel.get("name")):
            channel["name"] = "默认渠道"
        channels[index] = channel
        payload["model_channels"] = channels
        payload["active_model_channel_id"] = channel.get("id")
        return _mirror_active_to_legacy(payload)

    channel = {
        "id": _new_channel_id(),
        "name": "默认渠道",
        "base_url": normalize_openai_base_url(base_url) if base_url else base_url,
        "api_key": api_key,
        "model": model,
        "streaming_enabled": streaming,
        "created_at": now,
        "updated_at": now,
    }
    payload["model_channels"] = [channel]
    payload["active_model_channel_id"] = channel["id"]
    return _mirror_active_to_legacy(payload)


def _needs_channel_migration_persist(
    stored: dict[str, Any],
    payload: dict[str, Any],
) -> bool:
    """True when stored JSON lacks channels but legacy model triple is present."""
    if _channels_list(stored if isinstance(stored, dict) else {}):
        return False
    base_url = _clean_string(payload.get("model_base_url"))
    api_key = _clean_string(payload.get("model_api_key"))
    return bool(base_url and api_key)


def _ensure_channel_shape(settings_payload: dict[str, Any]) -> dict[str, Any]:
    payload = deepcopy(settings_payload)
    if "model_channels" not in payload or not isinstance(payload.get("model_channels"), list):
        payload["model_channels"] = []
    if "active_model_channel_id" not in payload:
        payload["active_model_channel_id"] = None

    channels = _channels_list(payload)
    # Migrate legacy single-slot config into channels when channels empty.
    if not channels:
        base_url = _clean_string(payload.get("model_base_url"))
        api_key = _clean_string(payload.get("model_api_key"))
        model = _clean_string(payload.get("model_name"))
        if base_url and api_key:
            now = _utc_now_iso()
            channel = {
                "id": _new_channel_id(),
                "name": "默认渠道",
                "base_url": (
                    normalize_openai_base_url(base_url) if base_url else base_url
                ),
                "api_key": api_key,
                "model": model,
                "streaming_enabled": bool(payload.get("model_streaming_enabled", True)),
                "created_at": now,
                "updated_at": now,
            }
            channels = [channel]
            payload["model_channels"] = channels
            payload["active_model_channel_id"] = channel["id"]

    active_id = _clean_string(payload.get("active_model_channel_id"))
    if channels and (
        not active_id or _channel_index(channels, active_id) is None
    ):
        payload["active_model_channel_id"] = channels[0].get("id")

    # Keep legacy flat fields aligned with active channel for old clients.
    return _mirror_active_to_legacy(payload)
