from app.db.session import SQLITE_CONNECT_ARGS
from app.main import bootstrap_sqlite_runtime

from sqlalchemy import create_engine, text


def test_sqlite_connect_args_wait_for_short_write_locks() -> None:
    assert SQLITE_CONNECT_ARGS["check_same_thread"] is False
    assert SQLITE_CONNECT_ARGS["timeout"] >= 30.0


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
