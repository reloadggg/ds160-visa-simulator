from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import secrets

from fastapi import HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy.orm import Session
from starlette import status
from starlette.responses import JSONResponse

from app.core.settings import settings
from app.db.models import AuthSessionRecord
from app.db.session import SessionLocal


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
BASE_PUBLIC_PATHS = {
    "/healthz",
    "/v1/auth/login",
    "/v1/auth/me",
}
DOC_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}
LOGIN_FAILURES: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    authenticated: bool = True
    expires_in: int


class AuthStatusResponse(BaseModel):
    authenticated: bool
    expires_at: str | None = None


@dataclass(frozen=True)
class CreatedAuthSession:
    session_id: str
    expires_at: datetime


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _client_ip(request: Request) -> str:
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",", 1)[0].strip()
    return request.client.host if request.client else "unknown"


def _rate_limit_key(request: Request) -> str:
    return _hash_secret(_client_ip(request))


def _prune_login_failures(key: str, now: datetime) -> list[float]:
    window_start = now.timestamp() - settings.app_auth_login_rate_limit_window_seconds
    failures = [item for item in LOGIN_FAILURES.get(key, []) if item >= window_start]
    if failures:
        LOGIN_FAILURES[key] = failures
    else:
        LOGIN_FAILURES.pop(key, None)
    return failures


def _check_login_rate_limit(request: Request, now: datetime) -> None:
    key = _rate_limit_key(request)
    failures = _prune_login_failures(key, now)
    if len(failures) >= settings.app_auth_login_rate_limit_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
        )


def _record_login_failure(request: Request, now: datetime) -> None:
    key = _rate_limit_key(request)
    failures = _prune_login_failures(key, now)
    failures.append(now.timestamp())
    LOGIN_FAILURES[key] = failures


def _clear_login_failures(request: Request) -> None:
    LOGIN_FAILURES.pop(_rate_limit_key(request), None)


def _request_fingerprint(request: Request) -> tuple[str | None, str | None]:
    user_agent = request.headers.get("user-agent")
    ip = _client_ip(request)
    return (
        _hash_secret(user_agent) if user_agent else None,
        _hash_secret(ip) if ip else None,
    )


def create_auth_session(
    db: Session,
    request: Request,
    *,
    now: datetime | None = None,
) -> CreatedAuthSession:
    current_time = now or _utcnow()
    session_id = secrets.token_urlsafe(32)
    user_agent_hash, ip_hash = _request_fingerprint(request)
    record = AuthSessionRecord(
        session_id_hash=_hash_secret(session_id),
        created_at=current_time,
        last_seen_at=current_time,
        expires_at=current_time + timedelta(seconds=settings.app_auth_session_ttl_seconds),
        user_agent_hash=user_agent_hash,
        ip_hash=ip_hash,
    )
    db.add(record)
    db.commit()
    return CreatedAuthSession(session_id=session_id, expires_at=record.expires_at)


def authenticate_password(
    password: str,
    request: Request,
    db: Session,
    *,
    now: datetime | None = None,
) -> CreatedAuthSession:
    current_time = now or _utcnow()
    configured_password = settings.app_auth_password
    if not configured_password:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="app auth is not enabled",
        )

    _check_login_rate_limit(request, current_time)
    if not hmac.compare_digest(password, configured_password):
        _record_login_failure(request, current_time)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    _clear_login_failures(request)
    return create_auth_session(db, request, now=current_time)


def _get_auth_session(
    db: Session,
    session_id: str | None,
    *,
    now: datetime | None = None,
    touch: bool = True,
) -> AuthSessionRecord | None:
    if not session_id:
        return None
    record = db.get(AuthSessionRecord, _hash_secret(session_id))
    if record is None:
        return None
    current_time = now or _utcnow()
    if record.revoked_at is not None:
        return None
    if record.expires_at <= current_time:
        return None
    idle_timeout = settings.app_auth_idle_timeout_seconds
    if idle_timeout > 0 and record.last_seen_at + timedelta(seconds=idle_timeout) <= current_time:
        return None
    if touch and _should_touch_auth_session(record, current_time):
        record.last_seen_at = current_time
        db.add(record)
        db.commit()
    return record


def _should_touch_auth_session(
    record: AuthSessionRecord,
    current_time: datetime,
) -> bool:
    touch_interval = settings.app_auth_touch_interval_seconds
    if touch_interval <= 0:
        return True
    return record.last_seen_at + timedelta(seconds=touch_interval) <= current_time


def get_current_auth_session(
    request: Request,
    db: Session,
    *,
    now: datetime | None = None,
    touch: bool = True,
) -> AuthSessionRecord | None:
    return _get_auth_session(
        db,
        request.cookies.get(settings.app_auth_cookie_name),
        now=now,
        touch=touch,
    )


def revoke_current_auth_session(request: Request, db: Session) -> None:
    record = _get_auth_session(
        db,
        request.cookies.get(settings.app_auth_cookie_name),
        touch=False,
    )
    if record is None:
        return
    record.revoked_at = _utcnow()
    db.add(record)
    db.commit()


def set_auth_cookie(response: Response, session_id: str, expires_at: datetime) -> None:
    response.set_cookie(
        key=settings.app_auth_cookie_name,
        value=session_id,
        max_age=settings.app_auth_session_ttl_seconds,
        expires=expires_at.replace(tzinfo=timezone.utc),
        httponly=True,
        secure=settings.app_auth_cookie_secure,
        samesite=settings.app_auth_cookie_samesite,
        domain=settings.app_auth_cookie_domain,
        path="/",
    )


def clear_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.app_auth_cookie_name,
        domain=settings.app_auth_cookie_domain,
        path="/",
    )


def _is_public_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path.rstrip("/") or "/"
    if path in BASE_PUBLIC_PATHS:
        return True
    return settings.app_auth_docs_public and path in DOC_PATHS


def _origin_allowed(request: Request) -> bool:
    origin = request.headers.get("origin")
    referer = request.headers.get("referer")
    expected_origins = {
        f"{request.url.scheme}://{request.headers.get('host')}",
        *settings.cors_allow_origins_list,
    }
    forwarded_host = request.headers.get("x-forwarded-host")
    if forwarded_host:
        forwarded_scheme = request.headers.get("x-forwarded-proto", request.url.scheme)
        expected_origins.add(f"{forwarded_scheme}://{forwarded_host}")

    if origin:
        return origin in expected_origins
    if referer:
        return any(referer.startswith(f"{expected_origin}/") for expected_origin in expected_origins)
    return False


def _machine_api_authorized(request: Request) -> bool:
    if request.url.path.rstrip("/") not in {
        "/v1/chat/completions",
        "/v1/responses",
    }:
        return False
    if not settings.app_compat_api_key:
        return False
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    return scheme.lower() == "bearer" and hmac.compare_digest(
        token,
        settings.app_compat_api_key,
    )


def _unauthorized_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_401_UNAUTHORIZED,
        content={"detail": "authentication required"},
    )


def _csrf_response() -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_403_FORBIDDEN,
        content={"detail": "csrf validation failed"},
    )


async def simple_auth_middleware(request: Request, call_next) -> Response:
    if not settings.app_auth_enabled or _is_public_request(request):
        return await call_next(request)

    if _machine_api_authorized(request):
        return await call_next(request)

    session_factory = getattr(request.app.state, "auth_session_factory", None) or SessionLocal
    with session_factory() as db:
        if get_current_auth_session(request, db) is None:
            return _unauthorized_response()

    if (
        settings.app_auth_csrf_protection
        and request.method not in SAFE_METHODS
        and not _origin_allowed(request)
    ):
        return _csrf_response()

    return await call_next(request)
