"""AdminConfigService product defaults for practice materials."""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import AdminSettingRecord
from app.services.admin_config_service import (
    DEFAULT_DEMO_SETTINGS,
    AdminConfigService,
)


def _session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'admin-config.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    return testing_session_local, engine


def test_default_demo_settings_practice_materials_enabled_true() -> None:
    assert DEFAULT_DEMO_SETTINGS["practice_materials_enabled"] is True


def test_practice_materials_enabled_true_when_no_admin_row(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            service = AdminConfigService(db)
            assert service.practice_materials_enabled() is True
            assert service.get_settings()["practice_materials_enabled"] is True
            assert service.public_app_config()["practice_materials_enabled"] is True
            assert service.material_generation_enabled() is True
    finally:
        engine.dispose()


def test_practice_materials_enabled_true_when_key_missing_from_stored_settings(
    tmp_path,
) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add(
                AdminSettingRecord(
                    setting_key="demo",
                    value_json={
                        "debug_console_enabled": False,
                        "debug_material_enabled": False,
                        # intentionally omit practice_materials_enabled
                    },
                )
            )
            db.commit()

            service = AdminConfigService(db)
            assert "practice_materials_enabled" not in (
                db.get(AdminSettingRecord, "demo").value_json or {}
            )
            assert service.practice_materials_enabled() is True
            assert service.get_settings()["practice_materials_enabled"] is True
            assert service.public_app_config()["practice_materials_enabled"] is True
    finally:
        engine.dispose()


def test_practice_materials_enabled_false_when_explicitly_disabled(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add(
                AdminSettingRecord(
                    setting_key="demo",
                    value_json={
                        "debug_console_enabled": False,
                        "debug_material_enabled": False,
                        "practice_materials_enabled": False,
                    },
                )
            )
            db.commit()

            service = AdminConfigService(db)
            assert service.practice_materials_enabled() is False
            assert service.get_settings()["practice_materials_enabled"] is False
            assert service.public_app_config()["practice_materials_enabled"] is False
            assert service.material_generation_enabled() is False
    finally:
        engine.dispose()


def test_material_generation_enabled_when_only_debug_material_on(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add(
                AdminSettingRecord(
                    setting_key="demo",
                    value_json={
                        "debug_console_enabled": True,
                        "debug_material_enabled": True,
                        "practice_materials_enabled": False,
                    },
                )
            )
            db.commit()

            service = AdminConfigService(db)
            assert service.practice_materials_enabled() is False
            assert service.debug_material_enabled() is True
            assert service.material_generation_enabled() is True
    finally:
        engine.dispose()


def test_debug_material_independent_of_practice_flag(tmp_path) -> None:
    factory, engine = _session_factory(tmp_path)
    try:
        with factory() as db:
            db.add(
                AdminSettingRecord(
                    setting_key="demo",
                    value_json={
                        "debug_console_enabled": False,
                        "debug_material_enabled": True,
                        "practice_materials_enabled": True,
                    },
                )
            )
            db.commit()

            service = AdminConfigService(db)
            assert service.practice_materials_enabled() is True
            # debug_material requires console AND materials
            assert service.debug_material_enabled() is False
            assert service.material_generation_enabled() is True
    finally:
        engine.dispose()
