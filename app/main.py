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


SESSION_RUNTIME_COLUMN_DEFS = {
    "gate_status_json": ("JSON", "'{}'"),
    "runtime_trace_json": ("JSON", "'[]'"),
    "score_history_json": ("JSON", "'[]'"),
    "governor_history_json": ("JSON", "'[]'"),
}


def bootstrap_sessions_table(db_engine: Engine) -> None:
    if db_engine.dialect.name != "sqlite":
        return

    inspector = inspect(db_engine)
    if "sessions" not in inspector.get_table_names():
        return

    existing_columns = {column["name"] for column in inspector.get_columns("sessions")}
    missing_columns = [
        column_name
        for column_name in SESSION_RUNTIME_COLUMN_DEFS
        if column_name not in existing_columns
    ]
    if not missing_columns:
        return

    with db_engine.begin() as connection:
        for column_name in missing_columns:
            column_type, default_value = SESSION_RUNTIME_COLUMN_DEFS[column_name]
            connection.execute(
                text(
                    "ALTER TABLE sessions "
                    f"ADD COLUMN {column_name} {column_type} "
                    f"NOT NULL DEFAULT {default_value}"
                )
            )


app = FastAPI(title="DS-160 Visa Simulator", version="0.1.0")
Base.metadata.create_all(bind=engine)
bootstrap_sessions_table(engine)
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
