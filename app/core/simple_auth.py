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
from app.db.models import AuthLoginEventRecord, AuthSessionRecord
from app.db.session import SessionLocal
from app.services.access_key_service import AccessKeyService


SAFE_METHODS = {"GET", "HEAD", "OPTIONS", "TRACE"}
BASE_PUBLIC_PATHS = {
    "/healthz",
    "/livez",
    "/version",
    "/v1/auth/login",
    "/v1/auth/me",
    "/v1/admin/login",
    "/v1/admin/me",
    "/v1/app-config",
}
DOC_PATHS = {
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
}
PUBLIC_PREFIXES = (
    "/v1/wx/upload-tickets/",
)
LOGIN_FAILURES: dict[str, list[float]] = {}


class LoginRequest(BaseModel):
    password: str


class AccessKeyQuotaResponse(BaseModel):
    key_id: str
    label: str = ""
    usage_limit: int
    usage_count: int
    remaining_uses: int
    can_create_session: bool
    expires_at: str | None = None
    revoked: bool = False
    revoked_at: str | None = None


class LoginResponse(BaseModel):
    authenticated: bool = True
    expires_in: int
    history_namespace: str = "local-dev"
    access_key_quota: AccessKeyQuotaResponse | None = None


class AuthStatusResponse(BaseModel):
    authenticated: bool
    expires_at: str | None = None
    history_namespace: str | None = None
    access_key_quota: AccessKeyQuotaResponse | None = None


@dataclass(frozen=True)
class CreatedAuthSession:
    session_id: str
    expires_at: datetime
    session_kind: str = "user"
    access_key_id: str | None = None


@dataclass(frozen=True)
class ClientRequestMetadata:
    client_ip: str
    client_ip_source: str
    user_agent: str | None = None
    cf_ray: str | None = None
    cf_country: str | None = None


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _first_header_value(value: str | None) -> str | None:
    if not value:
        return None
    first_value = value.split(",", 1)[0].strip()
    return first_value or None


def _rightmost_forwarded_ip(header_value: str | None) -> str | None:
    """Return the rightmost hop from X-Forwarded-For.

    When the app is behind a trusted reverse proxy that appends the connecting
    client address, the rightmost value is the one added by that proxy. The
    leftmost value is client-controlled and must not be trusted blindly.
    """
    if not header_value:
        return None
    hops = [part.strip() for part in header_value.split(",") if part.strip()]
    if not hops:
        return None
    return hops[-1]


def request_metadata(request: Request) -> ClientRequestMetadata:
    """Extract client metadata for auth, audit, and rate limiting.

    IP resolution order:
    1. ``CF-Connecting-IP`` when present (Cloudflare edge sets this; spoofable
       only if the origin is reachable without Cloudflare).
    2. When ``trust_x_forwarded_for`` is enabled: rightmost ``X-Forwarded-For``
       hop, else ``X-Real-IP``.
    3. Otherwise: the direct TCP peer (``request.client.host``).

    With ``trust_x_forwarded_for=false`` (default), client-supplied proxy
    headers other than CF-Connecting-IP are ignored so forged XFF cannot
    bypass login rate limits.
    """

    cf_connecting_ip = _first_header_value(request.headers.get("cf-connecting-ip"))
    if cf_connecting_ip:
        return ClientRequestMetadata(
            client_ip=cf_connecting_ip,
            client_ip_source="cf-connecting-ip",
            user_agent=request.headers.get("user-agent"),
            cf_ray=request.headers.get("cf-ray"),
            cf_country=request.headers.get("cf-ipcountry"),
        )

    if settings.trust_x_forwarded_for:
        forwarded_for = _rightmost_forwarded_ip(request.headers.get("x-forwarded-for"))
        if forwarded_for:
            return ClientRequestMetadata(
                client_ip=forwarded_for,
                client_ip_source="x-forwarded-for",
                user_agent=request.headers.get("user-agent"),
                cf_ray=request.headers.get("cf-ray"),
                cf_country=request.headers.get("cf-ipcountry"),
            )
        real_ip = _first_header_value(request.headers.get("x-real-ip"))
        if real_ip:
            return ClientRequestMetadata(
                client_ip=real_ip,
                client_ip_source="x-real-ip",
                user_agent=request.headers.get("user-agent"),
                cf_ray=request.headers.get("cf-ray"),
                cf_country=request.headers.get("cf-ipcountry"),
            )

    if request.client and request.client.host:
        client_ip = request.client.host
        client_ip_source = "direct"
    else:
        client_ip = "unknown"
        client_ip_source = "unknown"

    return ClientRequestMetadata(
        client_ip=client_ip,
        client_ip_source=client_ip_source,
        user_agent=request.headers.get("user-agent"),
        cf_ray=request.headers.get("cf-ray"),
        cf_country=request.headers.get("cf-ipcountry"),
    )


def _client_ip(request: Request) -> str:
    return request_metadata(request).client_ip


def _rate_limit_key(request: Request, *, scope: str = "user") -> str:
    return _hash_secret(f"{scope}:{_client_ip(request)}")


def _prune_login_failures(key: str, now: datetime) -> list[float]:
    window_start = now.timestamp() - settings.app_auth_login_rate_limit_window_seconds
    failures = [item for item in LOGIN_FAILURES.get(key, []) if item >= window_start]
    if failures:
        LOGIN_FAILURES[key] = failures
    else:
        LOGIN_FAILURES.pop(key, None)
    return failures


def check_login_rate_limit(
    request: Request,
    now: datetime,
    *,
    scope: str = "user",
) -> None:
    key = _rate_limit_key(request, scope=scope)
    failures = _prune_login_failures(key, now)
    if len(failures) >= settings.app_auth_login_rate_limit_attempts:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many login attempts",
        )


def record_login_failure(
    request: Request,
    now: datetime,
    *,
    scope: str = "user",
) -> None:
    key = _rate_limit_key(request, scope=scope)
    failures = _prune_login_failures(key, now)
    failures.append(now.timestamp())
    LOGIN_FAILURES[key] = failures


def clear_login_failures(request: Request, *, scope: str = "user") -> None:
    LOGIN_FAILURES.pop(_rate_limit_key(request, scope=scope), None)


# Backward-compatible private aliases used by this module.
def _check_login_rate_limit(request: Request, now: datetime) -> None:
    check_login_rate_limit(request, now, scope="user")


def _record_login_failure(request: Request, now: datetime) -> None:
    record_login_failure(request, now, scope="user")


def _clear_login_failures(request: Request) -> None:
    clear_login_failures(request, scope="user")


def _request_fingerprint(request: Request) -> tuple[str | None, str | None]:
    metadata = request_metadata(request)
    return (
        _hash_secret(metadata.user_agent) if metadata.user_agent else None,
        _hash_secret(metadata.client_ip) if metadata.client_ip else None,
    )


def record_login_audit_event(
    db: Session,
    request: Request,
    *,
    session_kind: str,
    outcome: str,
    occurred_at: datetime | None = None,
    access_key_id: str | None = None,
    session_id_hash: str | None = None,
    failure_reason: str | None = None,
) -> AuthLoginEventRecord:
    metadata = request_metadata(request)
    user_agent_hash = _hash_secret(metadata.user_agent) if metadata.user_agent else None
    record = AuthLoginEventRecord(
        occurred_at=occurred_at or _utcnow(),
        session_kind=session_kind,
        outcome=outcome,
        client_ip=metadata.client_ip,
        client_ip_source=metadata.client_ip_source,
        access_key_id=access_key_id,
        session_id_hash=session_id_hash,
        failure_reason=failure_reason,
        user_agent_hash=user_agent_hash,
        cf_ray=metadata.cf_ray,
        cf_country=metadata.cf_country,
    )
    db.add(record)
    return record


def create_auth_session(
    db: Session,
    request: Request,
    *,
    now: datetime | None = None,
    session_kind: str = "user",
    access_key_id: str | None = None,
    ttl_seconds: int | None = None,
) -> CreatedAuthSession:
    current_time = now or _utcnow()
    effective_ttl = ttl_seconds or settings.app_auth_session_ttl_seconds
    session_id = secrets.token_urlsafe(32)
    user_agent_hash, ip_hash = _request_fingerprint(request)
    record = AuthSessionRecord(
        session_id_hash=_hash_secret(session_id),
        session_kind=session_kind,
        access_key_id=access_key_id,
        created_at=current_time,
        last_seen_at=current_time,
        expires_at=current_time + timedelta(seconds=effective_ttl),
        user_agent_hash=user_agent_hash,
        ip_hash=ip_hash,
    )
    db.add(record)
    db.commit()
    return CreatedAuthSession(
        session_id=session_id,
        expires_at=record.expires_at,
        session_kind=session_kind,
        access_key_id=access_key_id,
    )


def authenticate_access_key(
    access_key: str,
    request: Request,
    db: Session,
    *,
    now: datetime | None = None,
    skip_rate_limit_check: bool = False,
) -> CreatedAuthSession | None:
    current_time = now or _utcnow()
    if not skip_rate_limit_check:
        _check_login_rate_limit(request, current_time)
    record = AccessKeyService(db).lookup_login_key(access_key)
    if record is None:
        return None
    _clear_login_failures(request)
    return create_auth_session(
        db,
        request,
        now=current_time,
        session_kind="user",
        access_key_id=record.key_id,
    )


def authenticate_password(
    password: str,
    request: Request,
    db: Session,
    *,
    now: datetime | None = None,
) -> CreatedAuthSession:
    current_time = now or _utcnow()
    try:
        _check_login_rate_limit(request, current_time)
    except HTTPException:
        record_login_audit_event(
            db,
            request,
            session_kind="user",
            outcome="failure",
            occurred_at=current_time,
            failure_reason="rate_limited",
        )
        db.commit()
        raise

    access_key_session = authenticate_access_key(
        password,
        request,
        db,
        now=current_time,
        skip_rate_limit_check=True,
    )
    if access_key_session is not None:
        record_login_audit_event(
            db,
            request,
            session_kind="user",
            outcome="success",
            occurred_at=current_time,
            access_key_id=access_key_session.access_key_id,
            session_id_hash=_hash_secret(access_key_session.session_id),
        )
        db.commit()
        return access_key_session

    configured_password = settings.app_auth_password
    if not configured_password:
        _record_login_failure(request, current_time)
        record_login_audit_event(
            db,
            request,
            session_kind="user",
            outcome="failure",
            occurred_at=current_time,
            failure_reason="invalid_access_key",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid access key",
        )

    if (
        not settings.app_auth_password_user_fallback_enabled
        or not hmac.compare_digest(password, configured_password)
    ):
        _record_login_failure(request, current_time)
        record_login_audit_event(
            db,
            request,
            session_kind="user",
            outcome="failure",
            occurred_at=current_time,
            failure_reason="invalid_credentials",
        )
        db.commit()
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid credentials",
        )

    _clear_login_failures(request)
    auth_session = create_auth_session(db, request, now=current_time)
    record_login_audit_event(
        db,
        request,
        session_kind="user",
        outcome="success",
        occurred_at=current_time,
        session_id_hash=_hash_secret(auth_session.session_id),
    )
    db.commit()
    return auth_session


def _get_auth_session(
    db: Session,
    session_id: str | None,
    *,
    now: datetime | None = None,
    touch: bool = True,
    session_kind: str | None = None,
) -> AuthSessionRecord | None:
    if not session_id:
        return None
    record = db.get(AuthSessionRecord, _hash_secret(session_id))
    if record is None:
        return None
    if session_kind is not None and record.session_kind != session_kind:
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
        session_kind="user",
    )


def get_current_admin_session(
    request: Request,
    db: Session,
    *,
    now: datetime | None = None,
    touch: bool = True,
) -> AuthSessionRecord | None:
    return _get_auth_session(
        db,
        request.cookies.get(settings.admin_auth_cookie_name),
        now=now,
        touch=touch,
        session_kind="admin",
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


def revoke_current_admin_session(request: Request, db: Session) -> None:
    record = _get_auth_session(
        db,
        request.cookies.get(settings.admin_auth_cookie_name),
        touch=False,
        session_kind="admin",
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


def set_admin_auth_cookie(response: Response, session_id: str, expires_at: datetime) -> None:
    response.set_cookie(
        key=settings.admin_auth_cookie_name,
        value=session_id,
        max_age=settings.admin_auth_session_ttl_seconds,
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


def clear_admin_auth_cookie(response: Response) -> None:
    response.delete_cookie(
        key=settings.admin_auth_cookie_name,
        domain=settings.app_auth_cookie_domain,
        path="/",
    )


def _is_public_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path.rstrip("/") or "/"
    if path in BASE_PUBLIC_PATHS:
        return True
    if any(path.startswith(prefix) for prefix in PUBLIC_PREFIXES):
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


def machine_api_authorized(request: Request) -> bool:
    """True when the request presents the machine compat API bearer token.

    Middleware treats this principal as authenticated for OpenAI-compat writer
    routes only. Session ownership for those routes still applies via
    ``ensure_session_access(..., allow_machine_api=True)`` (admin-equivalent).
    """
    if request.url.path.rstrip("/") not in {
        "/v1/chat/completions",
        "/v1/responses",
    }:
        return False
    if not settings.app_compat_api_key:
        return False
    authorization = request.headers.get("authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return False
    return hmac.compare_digest(token, settings.app_compat_api_key)


def _machine_api_authorized(request: Request) -> bool:
    return machine_api_authorized(request)


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
    if _is_public_request(request):
        return await call_next(request)

    if _machine_api_authorized(request):
        return await call_next(request)

    session_factory = getattr(request.app.state, "auth_session_factory", None) or SessionLocal
    with session_factory() as db:
        if request.url.path.startswith("/v1/admin"):
            if get_current_admin_session(request, db) is None:
                return _unauthorized_response()
            if (
                settings.app_auth_csrf_protection
                and request.method not in SAFE_METHODS
                and not _origin_allowed(request)
            ):
                return _csrf_response()
            return await call_next(request)

        if not settings.app_auth_enabled:
            return await call_next(request)
        if get_current_admin_session(request, db) is not None:
            if (
                settings.app_auth_csrf_protection
                and request.method not in SAFE_METHODS
                and not _origin_allowed(request)
            ):
                return _csrf_response()
            return await call_next(request)
        if get_current_auth_session(request, db) is None:
            return _unauthorized_response()

    if (
        settings.app_auth_csrf_protection
        and request.method not in SAFE_METHODS
        and not _origin_allowed(request)
    ):
        return _csrf_response()

    return await call_next(request)
