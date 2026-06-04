from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.simple_auth import get_current_admin_session, get_current_auth_session
from app.core.settings import settings
from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
from app.services.access_key_service import AccessKeyService


def get_session_repo(
    db: Session = Depends(get_db),
) -> SessionRepository:
    return SessionRepository(db)


def current_access_key_id(
    request: Request,
    db: Session,
) -> str | None:
    admin_session = get_current_admin_session(request, db, touch=False)
    if admin_session is not None:
        return None
    auth_session = get_current_auth_session(request, db, touch=False)
    return auth_session.access_key_id if auth_session is not None else None


def require_session_access(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    if not settings.app_auth_enabled:
        return
    if get_current_admin_session(request, db, touch=False) is not None:
        return
    auth_session = get_current_auth_session(request, db, touch=False)
    if auth_session is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not auth_session.access_key_id:
        return
    if not AccessKeyService(db).session_owned_by_key(
        key_id=auth_session.access_key_id,
        session_id=session_id,
    ):
        raise HTTPException(status_code=403, detail="session is not available for this access key")
