from collections.abc import Generator
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./app.sqlite3")
SQLITE_CONNECT_ARGS = {"check_same_thread": False, "timeout": 30.0}

engine = create_engine(DATABASE_URL, connect_args=SQLITE_CONNECT_ARGS)
SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
