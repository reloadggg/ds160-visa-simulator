from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.api.routers.files import router as files_router
from app.api.routers.messages import router as messages_router
from app.api.routers.openai_compat import router as openai_compat_router
from app.api.routers.reports import router as reports_router
from app.api.routers.sessions import router as sessions_router
from app.core.settings import settings
from app.db.base import Base
from app.db import evidence_models as _evidence_models
from app.db.models import SessionTurnRecord
from app.db.session import engine
from app.workers.parse_worker import (
    start_parse_worker_runtime,
    stop_parse_worker_runtime,
)


SESSION_RUNTIME_COLUMN_DEFS = {
    "gate_status_json": ("JSON", "'{}'"),
    "runtime_trace_json": ("JSON", "'[]'"),
    "score_history_json": ("JSON", "'[]'"),
    "governor_history_json": ("JSON", "'[]'"),
    "interviewer_state_json": ("JSON", "'{}'"),
    "current_focus_json": ("JSON", "'{}'"),
}
DOCUMENT_COLUMN_DEFS = {
    "raw_bytes": ("BLOB", "X''"),
}
SESSION_TURN_COLUMN_DEFS = {
    "turn_index": ("INTEGER", "0"),
}
SESSION_TURN_ORDER_INDEX_NAME = "ux_session_turns_session_id_turn_index"


def _bootstrap_table_columns(
    db_engine: Engine,
    *,
    table_name: str,
    column_defs: dict[str, tuple[str, str]],
) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if table_name not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    missing_columns = [
        column_name
        for column_name in column_defs
        if column_name not in existing_columns
    ]
    if not missing_columns:
        return

    with db_engine.begin() as connection:
        for column_name in missing_columns:
            column_type, default_value = column_defs[column_name]
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} {column_type} "
                    f"NOT NULL DEFAULT {default_value}"
                )
            )


def bootstrap_sessions_table(db_engine: Engine) -> None:
    _bootstrap_table_columns(
        db_engine,
        table_name="sessions",
        column_defs=SESSION_RUNTIME_COLUMN_DEFS,
    )


def bootstrap_documents_table(db_engine: Engine) -> None:
    _bootstrap_table_columns(
        db_engine,
        table_name="documents",
        column_defs=DOCUMENT_COLUMN_DEFS,
    )


def _backfill_session_turn_indexes(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "session_turns" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("session_turns")}
    if "turn_index" not in columns:
        return

    with db_engine.begin() as connection:
        legacy_rows = connection.execute(
            text(
                """
                SELECT rowid, session_id
                FROM session_turns
                WHERE turn_index = 0
                ORDER BY session_id, rowid
                """
            )
        ).mappings().all()

        next_turn_index_by_session: dict[str, int] = {}
        for row in legacy_rows:
            session_id = row["session_id"]
            next_turn_index = next_turn_index_by_session.get(session_id)
            if next_turn_index is None:
                next_turn_index = connection.execute(
                    text(
                        """
                        SELECT COALESCE(MAX(turn_index), 0) + 1
                        FROM session_turns
                        WHERE session_id = :session_id
                        """
                    ),
                    {"session_id": session_id},
                ).scalar_one()
            connection.execute(
                text(
                    """
                    UPDATE session_turns
                    SET turn_index = :turn_index
                    WHERE rowid = :rowid
                    """
                ),
                {"turn_index": next_turn_index, "rowid": row["rowid"]},
            )
            next_turn_index_by_session[session_id] = next_turn_index + 1


def _bootstrap_session_turn_order_index(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "session_turns" not in inspector.get_table_names():
        return

    with db_engine.begin() as connection:
        connection.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {SESSION_TURN_ORDER_INDEX_NAME}
                ON session_turns (session_id, turn_index)
                """
            )
        )


def bootstrap_session_turns_table(db_engine: Engine) -> None:
    SessionTurnRecord.__table__.create(bind=db_engine, checkfirst=True)
    _bootstrap_table_columns(
        db_engine,
        table_name="session_turns",
        column_defs=SESSION_TURN_COLUMN_DEFS,
    )
    _backfill_session_turn_indexes(db_engine)
    _bootstrap_session_turn_order_index(db_engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_parse_worker_runtime(app)
    try:
        yield
    finally:
        await stop_parse_worker_runtime(app)


app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
Base.metadata.create_all(bind=engine)
bootstrap_sessions_table(engine)
bootstrap_documents_table(engine)
bootstrap_session_turns_table(engine)
app.include_router(sessions_router)
app.include_router(files_router)
app.include_router(messages_router)
app.include_router(reports_router)
app.include_router(openai_compat_router)

try:
    from chainlit.utils import mount_chainlit
except ModuleNotFoundError:
    mount_chainlit = None

if mount_chainlit is not None:
    mount_chainlit(
        app,
        target=str(Path(__file__).resolve().parents[1] / "chainlit_app.py"),
        path="/ui",
    )


@app.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}
