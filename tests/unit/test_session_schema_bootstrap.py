from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, SessionRecord
from app.main import bootstrap_documents_table, bootstrap_sessions_table


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
            )
        )
        db.commit()
        record = db.get(SessionRecord, "sess-legacy")

    assert record is not None
    assert record.gate_status_json == gate_status
    assert record.runtime_trace_json == []
    assert record.score_history_json == []
    assert record.governor_history_json == []


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
