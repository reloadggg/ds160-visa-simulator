from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.simple_auth import (
    get_current_admin_session,
    get_current_auth_session,
    machine_api_authorized,
)
from app.core.settings import settings
from app.core.visa_families import validate_declared_family
from app.db.models import SessionRecord
from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
from app.services.access_key_service import AccessKeyService
from app.services.gate_service import GateService


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


def ensure_session_access(
    session_id: str,
    request: Request,
    db: Session,
    *,
    allow_machine_api: bool = False,
) -> None:
    """Enforce session ownership for the current principal.

    Policy (when ``app_auth_enabled``):
    - Admin cookie: any session.
    - Machine API key (``APP_COMPAT_API_KEY``) when ``allow_machine_api``:
      admin-equivalent for session access (compat writer routes only).
    - User cookie with access_key_id: must own the session via AccessKeySession.
    - User cookie without access_key_id (password fallback): any session.
    - Unauthenticated: 401.
    """
    if not settings.app_auth_enabled:
        return
    if get_current_admin_session(request, db, touch=False) is not None:
        return
    if allow_machine_api and machine_api_authorized(request):
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
        raise HTTPException(
            status_code=403,
            detail="session is not available for this access key",
        )


def require_session_access(
    session_id: str,
    request: Request,
    db: Session = Depends(get_db),
) -> None:
    ensure_session_access(session_id, request, db, allow_machine_api=False)


def create_session_with_quota(
    *,
    request: Request,
    db: Session,
    declared_family: str | None,
    gate_status_json: dict | None = None,
    repo: SessionRepository | None = None,
) -> SessionRecord:
    """Create a session and consume access-key quota when applicable.

    Rolls back (deletes) the session if quota consumption fails so create +
    quota stay transactional from the caller's perspective.
    """
    session_repo = repo or SessionRepository(db)
    family = declared_family
    if family is not None:
        # Callers may already have validated; keep a final safety check for
        # shared helper usage from OpenAI-compat paths.
        try:
            family = validate_declared_family(family)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    gate_status = gate_status_json
    if gate_status is None:
        gate_status = GateService().initial_gate_status(family)

    record = session_repo.create(
        declared_family=family,
        gate_status_json=gate_status,
    )
    access_key_id = current_access_key_id(request, db)
    if access_key_id:
        try:
            AccessKeyService(db).consume_session_quota(
                key_id=access_key_id,
                session_id=record.session_id,
            )
        except PermissionError as exc:
            db.delete(record)
            db.commit()
            raise HTTPException(status_code=403, detail=str(exc)) from exc
    return record
