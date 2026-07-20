"""Multi-channel admin model provider configuration."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import AdminSettingRecord
from app.services.admin_config_service import AdminConfigService


def _session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-channels.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    return testing_session_local, engine


def test_legacy_flat_model_settings_migrate_to_default_channel(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add(
                AdminSettingRecord(
                    setting_key="demo",
                    value_json={
                        "model_base_url": "https://legacy.example/v1",
                        "model_api_key": "legacy-key",
                        "model_name": "legacy-model",
                        "model_streaming_enabled": True,
                    },
                )
            )
            db.commit()

            service = AdminConfigService(db)
            payload = service.admin_payload()
            channels = payload["model_channels"]
            assert len(channels) == 1
            assert channels[0]["name"] == "默认渠道"
            assert channels[0]["base_url"] == "https://legacy.example/v1"
            assert channels[0]["model"] == "legacy-model"
            assert channels[0]["api_key_configured"] is True
            assert "api_key" not in channels[0]
            assert payload["active_model_channel_id"] == channels[0]["id"]
            assert payload["model_name"] == "legacy-model"

            effective = service.effective_model_config()
            assert effective.source == "admin"
            assert effective.model == "legacy-model"
            assert effective.base_url == "https://legacy.example/v1"
            assert effective.api_key == "legacy-key"
            assert effective.channel_id == channels[0]["id"]
    finally:
        engine.dispose()


def test_create_activate_switch_and_delete_channels(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            service = AdminConfigService(db)
            first = service.create_model_channel(
                name="venlacy",
                base_url="https://a.example/v1",
                api_key="key-a",
                model="model-a",
                activate=True,
            )
            second = service.create_model_channel(
                name="yxxb",
                base_url="https://sub.yxxb.eu.cc",
                api_key="key-b",
                model="grok-4.5",
                activate=False,
            )
            assert second["base_url"] == "https://sub.yxxb.eu.cc/v1"
            assert service.effective_model_config().model == "model-a"
            assert service.effective_model_config().channel_name == "venlacy"

            activated = service.activate_model_channel(second["id"])
            assert activated["active_model_channel_id"] == second["id"]
            assert activated["model_name"] == "grok-4.5"
            effective = service.effective_model_config()
            assert effective.model == "grok-4.5"
            assert effective.api_key == "key-b"
            assert effective.channel_name == "yxxb"

            service.activate_model_channel(first["id"])
            assert service.effective_model_config().model == "model-a"

            # deleting active falls over to remaining channel
            service.activate_model_channel(second["id"])
            deleted = service.delete_model_channel(second["id"])
            assert deleted["deleted_channel_id"] == second["id"]
            assert deleted["active_model_channel_id"] == first["id"]
            assert len(deleted["model_channels"]) == 1
            assert service.effective_model_config().model == "model-a"
    finally:
        engine.dispose()


def test_patch_legacy_fields_updates_active_channel(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            service = AdminConfigService(db)
            channel = service.create_model_channel(
                name="primary",
                base_url="https://a.example/v1",
                api_key="key-a",
                model="model-a",
                activate=True,
            )
            service.update_settings(
                {
                    "model_name": "model-b",
                    "model_base_url": "https://b.example/v1",
                }
            )
            payload = service.admin_payload()
            assert payload["active_model_channel_id"] == channel["id"]
            assert payload["model_name"] == "model-b"
            active = next(
                item
                for item in payload["model_channels"]
                if item["id"] == channel["id"]
            )
            assert active["model"] == "model-b"
            assert active["base_url"] == "https://b.example/v1"
            # api key preserved when not patched
            assert service.effective_model_config().api_key == "key-a"
    finally:
        engine.dispose()
