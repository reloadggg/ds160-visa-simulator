import base64
import hashlib
import hmac
import time

from fastapi import HTTPException, Request
from pydantic import BaseModel
from starlette import status
from starlette.responses import JSONResponse, Response

from app.core.settings import settings


PUBLIC_PATHS = {
    "/healthz",
    "/openapi.json",
    "/docs",
    "/docs/oauth2-redirect",
    "/redoc",
    "/v1/auth/login",
}


class LoginRequest(BaseModel):
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def create_access_token(now: int | None = None) -> str:
    if not settings.app_auth_password:
        raise RuntimeError("app auth password is not configured")

    issued_at = int(now or time.time())
    payload = str(issued_at).encode()
    signature = hmac.new(
        settings.app_auth_password.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()
    raw_token = f"{issued_at}.{signature}".encode()
    return base64.urlsafe_b64encode(raw_token).decode()


def verify_access_token(token: str, now: int | None = None) -> bool:
    if not settings.app_auth_password:
        return True

    try:
        decoded = base64.urlsafe_b64decode(token.encode()).decode()
        issued_at_text, signature = decoded.split(".", 1)
        issued_at = int(issued_at_text)
    except (ValueError, TypeError):
        return False

    current_time = int(now or time.time())
    if issued_at > current_time:
        return False
    if current_time - issued_at > settings.app_auth_token_ttl_seconds:
        return False

    expected = hmac.new(
        settings.app_auth_password.encode(),
        issued_at_text.encode(),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(signature, expected)


def authenticate_password(password: str) -> LoginResponse:
    configured_password = settings.app_auth_password
    if not configured_password:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="app auth is not enabled",
        )
    if not hmac.compare_digest(password, configured_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid password",
        )
    return LoginResponse(
        access_token=create_access_token(),
        expires_in=settings.app_auth_token_ttl_seconds,
    )


def _is_public_request(request: Request) -> bool:
    if request.method == "OPTIONS":
        return True
    path = request.url.path.rstrip("/") or "/"
    return path in PUBLIC_PATHS


def _extract_bearer_token(request: Request) -> str | None:
    authorization = request.headers.get("authorization")
    if not authorization:
        return request.query_params.get("access_token")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return request.query_params.get("access_token")
    return token


async def simple_auth_middleware(request: Request, call_next) -> Response:
    if not settings.app_auth_enabled or _is_public_request(request):
        return await call_next(request)

    token = _extract_bearer_token(request)
    if token is None or not verify_access_token(token):
        return JSONResponse(
            status_code=status.HTTP_401_UNAUTHORIZED,
            content={"detail": "authentication required"},
            headers={"WWW-Authenticate": "Bearer"},
        )

    return await call_next(request)
