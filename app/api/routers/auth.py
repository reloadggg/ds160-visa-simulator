from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.core.simple_auth import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
    authenticate_password,
    clear_auth_cookie,
    get_current_auth_session,
    revoke_current_auth_session,
    set_auth_cookie,
)
from app.db.session import get_db


router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    auth_session = authenticate_password(payload.password, request, db)
    set_auth_cookie(response, auth_session.session_id, auth_session.expires_at)
    return LoginResponse(
        expires_in=settings.app_auth_session_ttl_seconds,
        history_namespace=(
            f"key_{auth_session.access_key_id}"
            if auth_session.access_key_id
            else "local-dev"
        ),
    )


@router.get("/me", response_model=AuthStatusResponse)
def me(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    if not settings.app_auth_enabled:
        return AuthStatusResponse(authenticated=True)
    record = get_current_auth_session(request, db, touch=False)
    if record is None:
        return AuthStatusResponse(authenticated=False)
    return AuthStatusResponse(
        authenticated=True,
        expires_at=record.expires_at.isoformat() + "Z",
        history_namespace=(
            f"key_{record.access_key_id}"
            if record.access_key_id
            else "local-dev"
        ),
    )


@router.post("/logout", response_model=AuthStatusResponse)
def logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    revoke_current_auth_session(request, db)
    clear_auth_cookie(response)
    return AuthStatusResponse(authenticated=False)
