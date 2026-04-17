from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.session_repo import SessionRepository


def get_session_repo(
    db: Session = Depends(get_db),
) -> SessionRepository:
    return SessionRepository(db)
