from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, SessionRecord, SessionTurnRecord
from app.main import (
    bootstrap_documents_table,
    bootstrap_session_turns_table,
    bootstrap_sessions_table,
)


def test_bootstrap_sessions_table_adds_missing_runtime_columns(tmp_path) -> None:
    db_path = tmp_path / "legacy.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sessions (
                    session_id VARCHAR(64) PRIMARY KEY,
                    phase_state VARCHAR(32),
                    declared_family VARCHAR(32),
                    current_governor_decision VARCHAR(32),
                    profile_json JSON,
                    route_candidates_json JSON
                )
                """
            )
        )

    bootstrap_sessions_table(engine)
    bootstrap_sessions_table(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("sessions")}
    assert {
        "gate_status_json",
        "runtime_trace_json",
        "score_history_json",
        "governor_history_json",
        "interviewer_state_json",
        "current_focus_json",
    }.issubset(columns)

    gate_status = {
        "declared_family": "f1",
        "scenario_key": "parent_sponsored",
        "status": "pending_documents",
        "required_documents": [],
    }
    with Session(engine) as db:
        db.add(
            SessionRecord(
                session_id="sess-legacy",
                declared_family="f1",
                gate_status_json=gate_status,
                runtime_trace_json=[],
                score_history_json=[],
                governor_history_json=[],
                interviewer_state_json={"mode": "interviewing"},
                current_focus_json={"topic": "funding"},
            )
        )
        db.commit()
        record = db.get(SessionRecord, "sess-legacy")

    assert record is not None
    assert record.gate_status_json == gate_status
    assert record.runtime_trace_json == []
    assert record.score_history_json == []
    assert record.governor_history_json == []
    assert record.interviewer_state_json == {"mode": "interviewing"}
    assert record.current_focus_json == {"topic": "funding"}


def test_bootstrap_session_turns_table_creates_missing_table(tmp_path) -> None:
    db_path = tmp_path / "legacy-turns.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sessions (
                    session_id VARCHAR(64) PRIMARY KEY,
                    phase_state VARCHAR(32),
                    declared_family VARCHAR(32),
                    current_governor_decision VARCHAR(32),
                    profile_json JSON,
                    route_candidates_json JSON
                )
                """
            )
        )

    bootstrap_session_turns_table(engine)
    bootstrap_session_turns_table(engine)

    inspector = inspect(engine)
    assert "session_turns" in inspector.get_table_names()
    columns = {column["name"] for column in inspector.get_columns("session_turns")}
    assert {
        "turn_id",
        "turn_index",
        "session_id",
        "role",
        "content",
        "source",
        "metadata_json",
        "client_message_id",
    }.issubset(columns)

    with Session(engine) as db:
        db.add(
            SessionTurnRecord(
                turn_id="turn-1",
                turn_index=1,
                session_id="sess-legacy",
                role="user",
                content="我想解释一下我的资金来源。",
                source="chat",
                metadata_json={"channel": "api"},
            )
        )
        db.commit()
        record = db.get(SessionTurnRecord, "turn-1")

    assert record is not None
    assert record.content == "我想解释一下我的资金来源。"
    assert record.metadata_json == {"channel": "api"}


def test_bootstrap_session_turns_table_adds_missing_turn_index_column(tmp_path) -> None:
    db_path = tmp_path / "legacy-turn-index.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE session_turns (
                    turn_id VARCHAR(64) PRIMARY KEY,
                    session_id VARCHAR(64),
                    role VARCHAR(32),
                    content TEXT,
                    source VARCHAR(64),
                    metadata_json JSON
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO session_turns (
                    turn_id,
                    session_id,
                    role,
                    content,
                    source,
                    metadata_json
                ) VALUES
                    ('turn-b', 'sess-legacy', 'user', '第一轮', 'chat', '{}'),
                    ('turn-a', 'sess-legacy', 'assistant', '第二轮', 'chat', '{}')
                """
            )
        )

    bootstrap_session_turns_table(engine)
    bootstrap_session_turns_table(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("session_turns")}
    assert "turn_index" in columns
    indexes = inspect(engine).get_indexes("session_turns")
    assert any(
        index["unique"] and index["column_names"] == ["session_id", "turn_index"]
        for index in indexes
    )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT turn_id, turn_index
                FROM session_turns
                WHERE session_id = 'sess-legacy'
                ORDER BY turn_index
                """
            )
        ).all()

    assert rows == [("turn-b", 1), ("turn-a", 2)]


def test_bootstrap_session_turns_table_adds_client_message_id_column_and_index(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-client-message-id.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE session_turns (
                    turn_id VARCHAR(64) PRIMARY KEY,
                    turn_index INTEGER,
                    session_id VARCHAR(64),
                    role VARCHAR(32),
                    content TEXT,
                    source VARCHAR(64),
                    metadata_json JSON
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO session_turns (
                    turn_id,
                    turn_index,
                    session_id,
                    role,
                    content,
                    source,
                    metadata_json
                ) VALUES
                    (
                        'turn-user',
                        1,
                        'sess-legacy',
                        'user',
                        '第一轮',
                        'chat',
                        '{"client_message_id": "client-legacy-1"}'
                    ),
                    (
                        'turn-assistant',
                        2,
                        'sess-legacy',
                        'assistant',
                        '第二轮',
                        'chat',
                        '{}'
                    )
                """
            )
        )

    bootstrap_session_turns_table(engine)
    bootstrap_session_turns_table(engine)

    inspector = inspect(engine)
    columns = {column["name"] for column in inspector.get_columns("session_turns")}
    indexes = inspector.get_indexes("session_turns")
    assert "client_message_id" in columns
    assert any(
        index["unique"] and index["column_names"] == ["session_id", "client_message_id"]
        for index in indexes
    )

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT turn_id, client_message_id
                FROM session_turns
                ORDER BY turn_index
                """
            )
        ).all()

    assert rows == [
        ("turn-user", "client-legacy-1"),
        ("turn-assistant", None),
    ]


def test_bootstrap_session_turns_table_deduplicates_client_message_ids(
    tmp_path,
) -> None:
    db_path = tmp_path / "legacy-duplicate-client-message-id.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE session_turns (
                    turn_id VARCHAR(64) PRIMARY KEY,
                    turn_index INTEGER,
                    session_id VARCHAR(64),
                    role VARCHAR(32),
                    content TEXT,
                    source VARCHAR(64),
                    metadata_json JSON,
                    client_message_id VARCHAR(128)
                )
                """
            )
        )
        conn.execute(
            text(
                """
                INSERT INTO session_turns (
                    turn_id,
                    turn_index,
                    session_id,
                    role,
                    content,
                    source,
                    metadata_json,
                    client_message_id
                ) VALUES
                    (
                        'turn-user-1',
                        1,
                        'sess-legacy',
                        'user',
                        '第一轮',
                        'chat',
                        '{}',
                        'client-duplicate'
                    ),
                    (
                        'turn-user-2',
                        2,
                        'sess-legacy',
                        'user',
                        '重复第一轮',
                        'chat',
                        '{}',
                        'client-duplicate'
                    )
                """
            )
        )

    bootstrap_session_turns_table(engine)
    bootstrap_session_turns_table(engine)

    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT turn_id, client_message_id
                FROM session_turns
                ORDER BY turn_index
                """
            )
        ).all()

    assert rows == [
        ("turn-user-1", "client-duplicate"),
        ("turn-user-2", None),
    ]


def test_bootstrap_documents_table_adds_missing_raw_bytes_column(tmp_path) -> None:
    db_path = tmp_path / "legacy-documents.sqlite3"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )

    with engine.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE documents (
                    document_id VARCHAR(64) PRIMARY KEY,
                    session_id VARCHAR(64),
                    filename VARCHAR(255),
                    status VARCHAR(32),
                    artifact_json JSON,
                    raw_text TEXT
                )
                """
            )
        )

    bootstrap_documents_table(engine)
    bootstrap_documents_table(engine)

    columns = {column["name"] for column in inspect(engine).get_columns("documents")}
    assert "raw_bytes" in columns

    with Session(engine) as db:
        db.add(
            DocumentRecord(
                document_id="doc-legacy",
                session_id="sess-legacy",
                filename="passport_bio.txt",
                status="uploaded",
                artifact_json={"status": "uploaded"},
                raw_bytes=b"",
                raw_text="",
            )
        )
        db.commit()
        record = db.get(DocumentRecord, "doc-legacy")

    assert record is not None
    assert record.raw_bytes == b""
