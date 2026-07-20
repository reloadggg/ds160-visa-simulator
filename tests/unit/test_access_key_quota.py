"""Unit tests for atomic access-key session quota consumption."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import AccessKeyRecord, AccessKeySessionRecord
from app.services.access_key_service import AccessKeyService


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'access-key-quota.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.close()

    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_consume_session_quota_is_idempotent_for_same_session(db_session_factory) -> None:
    with db_session_factory() as db:
        created = AccessKeyService(db).create_key(label="quota", usage_limit=2)
        key_id = created.record.key_id
        AccessKeyService(db).consume_session_quota(key_id=key_id, session_id="sess-a")
        AccessKeyService(db).consume_session_quota(key_id=key_id, session_id="sess-a")
        record = db.get(AccessKeyRecord, key_id)
        assert record is not None
        assert record.usage_count == 1
        bindings = db.execute(
            select(AccessKeySessionRecord).where(AccessKeySessionRecord.key_id == key_id)
        ).scalars().all()
        assert len(bindings) == 1


def test_consume_session_quota_rejects_when_exhausted(db_session_factory) -> None:
    with db_session_factory() as db:
        created = AccessKeyService(db).create_key(label="quota", usage_limit=1)
        key_id = created.record.key_id
        AccessKeyService(db).consume_session_quota(key_id=key_id, session_id="sess-a")
        with pytest.raises(PermissionError, match="quota exhausted"):
            AccessKeyService(db).consume_session_quota(key_id=key_id, session_id="sess-b")
        record = db.get(AccessKeyRecord, key_id)
        assert record is not None
        assert record.usage_count == 1


def test_concurrent_consume_cannot_exceed_limit(db_session_factory) -> None:
    with db_session_factory() as db:
        created = AccessKeyService(db).create_key(label="race", usage_limit=3)
        key_id = created.record.key_id

    def attempt(session_id: str) -> str:
        with db_session_factory() as db:
            try:
                AccessKeyService(db).consume_session_quota(
                    key_id=key_id,
                    session_id=session_id,
                )
                return "ok"
            except PermissionError:
                return "denied"

    session_ids = [f"sess-{index}" for index in range(12)]
    results: list[str] = []
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(attempt, session_id) for session_id in session_ids]
        for future in as_completed(futures):
            results.append(future.result())

    assert results.count("ok") == 3
    assert results.count("denied") == 9

    with db_session_factory() as db:
        record = db.get(AccessKeyRecord, key_id)
        assert record is not None
        assert record.usage_count == 3
        bindings = db.execute(
            select(AccessKeySessionRecord).where(AccessKeySessionRecord.key_id == key_id)
        ).scalars().all()
        assert len(bindings) == 3
