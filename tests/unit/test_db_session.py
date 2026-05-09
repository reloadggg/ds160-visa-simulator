from app.db.session import SQLITE_CONNECT_ARGS


def test_sqlite_connect_args_wait_for_short_write_locks() -> None:
    assert SQLITE_CONNECT_ARGS["check_same_thread"] is False
    assert SQLITE_CONNECT_ARGS["timeout"] >= 30.0
