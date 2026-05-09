from fastapi import APIRouter

from app.core.simple_auth import LoginRequest, LoginResponse, authenticate_password


router = APIRouter(prefix="/v1/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest) -> LoginResponse:
    return authenticate_password(payload.password)
