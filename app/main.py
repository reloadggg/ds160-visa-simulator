from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from sqlalchemy import inspect, text
from sqlalchemy.engine import Engine

from app.api.routers.files import router as files_router
from app.api.routers.messages import router as messages_router
from app.api.routers.openai_compat import router as openai_compat_router
from app.api.routers.reports import router as reports_router
from app.api.routers.sessions import router as sessions_router
from app.db.base import Base
from app.db import evidence_models as _evidence_models
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
}
DOCUMENT_COLUMN_DEFS = {
    "raw_bytes": ("BLOB", "X''"),
}


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    await start_parse_worker_runtime(app)
    try:
        yield
    finally:
        await stop_parse_worker_runtime(app)


app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0", lifespan=lifespan)
Base.metadata.create_all(bind=engine)
bootstrap_sessions_table(engine)
bootstrap_documents_table(engine)
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
