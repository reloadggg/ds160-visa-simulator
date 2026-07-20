"""Unit tests for material generation rate limits and in-flight locks."""

from datetime import timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import SessionRecord
from app.services.material_generation_guard import (
    MaterialGenerationGuard,
    MaterialGenerationInProgressError,
    MaterialGenerationRateLimitError,
    reset_access_key_rate_limits_for_tests,
    _utc_now,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'material-gen-guard.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    db = factory()
    db.add(
        SessionRecord(
            session_id="sess-guard-1",
            declared_family="f1",
            gate_status_json={},
            interviewer_state_json={},
        )
    )
    db.commit()
    reset_access_key_rate_limits_for_tests()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()
        reset_access_key_rate_limits_for_tests()


def test_acquire_then_second_request_conflicts(db_session) -> None:
    guard = MaterialGenerationGuard(db_session)
    guard.acquire("sess-guard-1", bundle_id="b1")
    with pytest.raises(MaterialGenerationInProgressError) as exc:
        MaterialGenerationGuard(db_session).acquire("sess-guard-1")
    assert "already in progress" in exc.value.detail


def test_complete_releases_lock(db_session) -> None:
    guard = MaterialGenerationGuard(db_session)
    guard.acquire("sess-guard-1")
    guard.complete("sess-guard-1")
    MaterialGenerationGuard(db_session).acquire("sess-guard-1")


def test_stale_running_expires_by_ttl(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings_module.settings,
        "material_generation_lock_ttl_seconds",
        60,
    )
    guard = MaterialGenerationGuard(db_session)
    guard.acquire("sess-guard-1")
    record = db_session.get(SessionRecord, "sess-guard-1")
    assert record is not None
    state = dict(record.interviewer_state_json or {})
    mg = dict(state.get("material_generation") or {})
    mg["started_at"] = (_utc_now() - timedelta(seconds=120)).isoformat()
    state["material_generation"] = mg
    record.interviewer_state_json = state
    db_session.add(record)
    db_session.commit()

    MaterialGenerationGuard(db_session).acquire("sess-guard-1")


def test_session_rate_limit(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_module.settings, "material_generation_session_limit", 2)
    monkeypatch.setattr(
        settings_module.settings,
        "material_generation_session_window_seconds",
        3600,
    )
    guard = MaterialGenerationGuard(db_session)
    guard.acquire("sess-guard-1")
    guard.complete("sess-guard-1")
    guard.acquire("sess-guard-1")
    guard.complete("sess-guard-1")
    with pytest.raises(MaterialGenerationRateLimitError) as exc:
        guard.acquire("sess-guard-1")
    assert "session material generation rate limit" in exc.value.detail


def test_access_key_rate_limit(db_session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        settings_module.settings, "material_generation_access_key_limit", 2
    )
    monkeypatch.setattr(
        settings_module.settings,
        "material_generation_access_key_window_seconds",
        3600,
    )
    # Use distinct sessions so only the key limit applies.
    for index in range(2):
        sid = f"sess-key-{index}"
        db_session.add(
            SessionRecord(
                session_id=sid,
                declared_family="f1",
                gate_status_json={},
                interviewer_state_json={},
            )
        )
    db_session.commit()

    MaterialGenerationGuard(db_session).acquire("sess-key-0", access_key_id="key-a")
    MaterialGenerationGuard(db_session).complete("sess-key-0")
    MaterialGenerationGuard(db_session).acquire("sess-key-1", access_key_id="key-a")
    MaterialGenerationGuard(db_session).complete("sess-key-1")

    db_session.add(
        SessionRecord(
            session_id="sess-key-2",
            declared_family="f1",
            gate_status_json={},
            interviewer_state_json={},
        )
    )
    db_session.commit()
    with pytest.raises(MaterialGenerationRateLimitError) as exc:
        MaterialGenerationGuard(db_session).acquire(
            "sess-key-2", access_key_id="key-a"
        )
    assert "access key material generation rate limit" in exc.value.detail


def test_set_bundle_id_while_running(db_session) -> None:
    guard = MaterialGenerationGuard(db_session)
    guard.acquire("sess-guard-1", bundle_id="initial")
    guard.set_bundle_id("sess-guard-1", "updated-bundle")
    status = guard.get_status("sess-guard-1")
    assert status["status"] == "running"
    assert status["bundle_id"] == "updated-bundle"
