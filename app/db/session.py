from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.sqlite3")
SQLITE_CONNECT_ARGS = {"check_same_thread": False, "timeout": 30.0}


def connect_args_for_database_url(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return SQLITE_CONNECT_ARGS
    return {}


def engine_kwargs_for_database_url(database_url: str) -> dict[str, object]:
    if database_url.startswith("sqlite"):
        return {}
    return {"pool_pre_ping": True}


engine = create_engine(
    DATABASE_URL,
    connect_args=connect_args_for_database_url(DATABASE_URL),
    **engine_kwargs_for_database_url(DATABASE_URL),
)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def make_session_factory(bind: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=bind, autocommit=False, autoflush=False)


def session_factory_from_session(db: Session) -> sessionmaker[Session]:
    bind = db.get_bind()
    db.close()
    return make_session_factory(bind)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
