from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.api.routers.admin import router as admin_router
from app.api.routers.app_config import router as app_config_router
from app.api.routers.auth import router as auth_router
from app.api.routers.files import router as files_router
from app.api.routers.material_packages import router as material_packages_router
from app.api.routers.materials import router as materials_router
from app.api.routers.messages import router as messages_router
from app.api.routers.model_config import router as model_config_router
from app.api.routers.openai_compat import router as openai_compat_router
from app.api.routers.openai_responses import router as openai_responses_router
from app.api.routers.rag import router as rag_router
from app.api.routers.reports import router as reports_router
from app.api.routers.sessions import router as sessions_router
from app.api.routers.wx_upload import (
    session_router as wx_upload_session_router,
    ticket_router as wx_upload_ticket_router,
)
from app.core.app_version import APP_VERSION, backend_version_payload
from app.core.health import build_health_payload
from app.core.logging_config import configure_logging
from app.core.settings import settings
from app.core.simple_auth import simple_auth_middleware
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
AUTH_SESSION_COLUMN_DEFS = {
    "session_kind": ("VARCHAR(16)", "'user'"),
}
AUTH_SESSION_NULLABLE_COLUMN_DEFS = {
    "access_key_id": "VARCHAR(32)",
}
ACCESS_KEY_NULLABLE_COLUMN_DEFS = {
    "key_display_value": "TEXT",
}
SESSION_TURN_ORDER_INDEX_NAME = "ux_session_turns_session_id_turn_index"
SESSION_TURN_CLIENT_MESSAGE_INDEX_NAME = "ux_session_turns_session_id_client_message_id"


configure_logging(level=settings.log_level, log_format=settings.log_format)


def bootstrap_sqlite_runtime(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    with db_engine.begin() as connection:
        connection.execute(text("PRAGMA journal_mode=WAL"))
        connection.execute(text("PRAGMA busy_timeout=30000"))
        connection.execute(text("PRAGMA synchronous=NORMAL"))


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


def _bootstrap_nullable_table_columns(
    db_engine: Engine,
    *,
    table_name: str,
    column_defs: dict[str, str],
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
            connection.execute(
                text(
                    f"ALTER TABLE {table_name} "
                    f"ADD COLUMN {column_name} {column_defs[column_name]}"
                )
            )


def bootstrap_sessions_table(db_engine: Engine) -> None:
    _bootstrap_table_columns(
        db_engine,
        table_name="sessions",
        column_defs=SESSION_RUNTIME_COLUMN_DEFS,
    )


def bootstrap_auth_sessions_table(db_engine: Engine) -> None:
    _bootstrap_table_columns(
        db_engine,
        table_name="auth_sessions",
        column_defs=AUTH_SESSION_COLUMN_DEFS,
    )
    _bootstrap_nullable_table_columns(
        db_engine,
        table_name="auth_sessions",
        column_defs=AUTH_SESSION_NULLABLE_COLUMN_DEFS,
    )


def bootstrap_access_keys_table(db_engine: Engine) -> None:
    inspector = inspect(db_engine)
    if "access_keys" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("access_keys")}
    missing_columns = [
        column_name
        for column_name in ACCESS_KEY_NULLABLE_COLUMN_DEFS
        if column_name not in existing_columns
    ]
    if not missing_columns:
        return

    with db_engine.begin() as connection:
        for column_name in missing_columns:
            column_type = ACCESS_KEY_NULLABLE_COLUMN_DEFS[column_name]
            if db_engine.dialect.name == "postgresql":
                connection.execute(
                    text(
                        f"ALTER TABLE access_keys "
                        f"ADD COLUMN IF NOT EXISTS {column_name} {column_type}"
                    )
                )
            else:
                connection.execute(
                    text(
                        f"ALTER TABLE access_keys "
                        f"ADD COLUMN {column_name} {column_type}"
                    )
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


def _backfill_session_turn_client_message_ids(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "session_turns" not in inspector.get_table_names():
        return

    columns = {column["name"] for column in inspector.get_columns("session_turns")}
    if "client_message_id" in columns or "metadata_json" not in columns:
        return

    with db_engine.begin() as connection:
        connection.execute(
            text("ALTER TABLE session_turns ADD COLUMN client_message_id VARCHAR(128)")
        )
        connection.execute(
            text(
                """
                UPDATE session_turns
                SET client_message_id = json_extract(metadata_json, '$.client_message_id')
                WHERE role = 'user'
                  AND client_message_id IS NULL
                  AND json_extract(metadata_json, '$.client_message_id') IS NOT NULL
                """
            )
        )


def _bootstrap_session_turn_client_message_index(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "session_turns" not in inspector.get_table_names():
        return

    with db_engine.begin() as connection:
        connection.execute(
            text(
                """
                UPDATE session_turns
                SET client_message_id = NULL
                WHERE client_message_id IS NOT NULL
                  AND rowid NOT IN (
                      SELECT MIN(rowid)
                      FROM session_turns
                      WHERE client_message_id IS NOT NULL
                      GROUP BY session_id, client_message_id
                  )
                """
            )
        )
        connection.execute(
            text(
                f"""
                CREATE UNIQUE INDEX IF NOT EXISTS {SESSION_TURN_CLIENT_MESSAGE_INDEX_NAME}
                ON session_turns (session_id, client_message_id)
                WHERE client_message_id IS NOT NULL
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
    inspector = inspect(db_engine)
    columns = (
        {column["name"] for column in inspector.get_columns("session_turns")}
        if "session_turns" in inspector.get_table_names()
        else set()
    )
    if "client_message_id" not in columns:
        _backfill_session_turn_client_message_ids(db_engine)
    _bootstrap_session_turn_order_index(db_engine)
    _bootstrap_session_turn_client_message_index(db_engine)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_parse_worker_runtime(app)
    try:
        yield
    finally:
        await stop_parse_worker_runtime(app)


app = FastAPI(title="DS-160 Visa Simulator", version=APP_VERSION, lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_allow_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.middleware("http")(simple_auth_middleware)
bootstrap_sqlite_runtime(engine)
Base.metadata.create_all(bind=engine)
bootstrap_auth_sessions_table(engine)
bootstrap_access_keys_table(engine)
bootstrap_sessions_table(engine)
bootstrap_documents_table(engine)
bootstrap_session_turns_table(engine)
app.include_router(sessions_router)
app.include_router(app_config_router)
app.include_router(admin_router)
app.include_router(auth_router)
app.include_router(files_router)
app.include_router(material_packages_router)
app.include_router(materials_router)
app.include_router(messages_router)
app.include_router(model_config_router)
app.include_router(rag_router)
app.include_router(reports_router)
app.include_router(openai_compat_router)
app.include_router(openai_responses_router)
app.include_router(wx_upload_session_router)
app.include_router(wx_upload_ticket_router)


@app.get("/livez")
def livez() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/healthz")
def healthz(request: Request) -> JSONResponse:
    payload = build_health_payload(app=request.app, engine=engine, app_settings=settings)
    return JSONResponse(
        content=payload,
        status_code=200 if payload["status"] == "ok" else 503,
    )


@app.get("/version")
def version() -> dict[str, str | None]:
    return backend_version_payload()
