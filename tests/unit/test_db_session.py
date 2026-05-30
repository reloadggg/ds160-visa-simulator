from app.db import evidence_models as _evidence_models
from app.db.base import Base
from unittest.mock import Mock

from app.db.session import (
    SQLITE_CONNECT_ARGS,
    connect_args_for_database_url,
    engine_kwargs_for_database_url,
    session_factory_from_session,
)
from app.main import bootstrap_sqlite_runtime

from sqlalchemy import create_engine, create_mock_engine, text


def test_sqlite_connect_args_wait_for_short_write_locks() -> None:
    assert SQLITE_CONNECT_ARGS["check_same_thread"] is False
    assert SQLITE_CONNECT_ARGS["timeout"] >= 30.0


def test_connect_args_only_apply_to_sqlite() -> None:
    assert (
        connect_args_for_database_url("sqlite:///./app.sqlite3")
        == SQLITE_CONNECT_ARGS
    )
    assert (
        connect_args_for_database_url("postgresql+psycopg://app:pass@db/app")
        == {}
    )


def test_postgres_engine_uses_pre_ping() -> None:
    assert engine_kwargs_for_database_url("sqlite:///./app.sqlite3") == {}
    assert engine_kwargs_for_database_url("postgresql+psycopg://app:pass@db/app") == {
        "pool_pre_ping": True,
    }


def test_session_factory_from_session_releases_source_session(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'stream.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    source_session = Mock()
    source_session.get_bind.return_value = engine

    session_factory = session_factory_from_session(source_session)

    source_session.close.assert_called_once()
    with session_factory() as db:
        assert db.get_bind() is engine


def test_bootstrap_sqlite_runtime_enables_wal_and_busy_timeout(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime.sqlite3'}",
        connect_args={"check_same_thread": False},
    )

    bootstrap_sqlite_runtime(engine)

    with engine.connect() as connection:
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()
        busy_timeout = connection.execute(text("PRAGMA busy_timeout")).scalar_one()
        synchronous = connection.execute(text("PRAGMA synchronous")).scalar_one()

    assert journal_mode == "wal"
    assert busy_timeout >= 30000
    assert synchronous == 1


def test_postgres_schema_bootstrap_compiles_without_sqlite_pragmas() -> None:
    statements: list[str] = []

    engine = create_mock_engine(
        "postgresql+psycopg://",
        lambda sql, *args, **kwargs: statements.append(
            str(sql.compile(dialect=engine.dialect))
        ),
    )

    bootstrap_sqlite_runtime(engine)
    Base.metadata.create_all(bind=engine)

    assert statements
    assert not any("PRAGMA" in statement.upper() for statement in statements)
