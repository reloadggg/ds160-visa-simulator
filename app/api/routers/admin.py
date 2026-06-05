from __future__ import annotations

from datetime import datetime
import hmac
from typing import Any, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session
from starlette import status

from app.core.settings import settings
from app.core.simple_auth import (
    AuthStatusResponse,
    LoginRequest,
    LoginResponse,
    clear_admin_auth_cookie,
    create_auth_session,
    get_current_admin_session,
    revoke_current_admin_session,
    set_admin_auth_cookie,
)
from app.db.models import AccessKeySessionRecord, AuthSessionRecord, SessionRecord, SessionTurnRecord
from app.db.session import get_db
from app.agents.user_model_config import normalize_openai_base_url
from app.services.access_key_service import AccessKeyService
from app.services.admin_config_service import AdminConfigService
from app.services.admin_model_config_service import AdminModelConfigService
from app.services.runtime_errors import ModelRuntimeError
from app.services.visa_policy_ingest_service import PolicyKnowledgeIngestService

router = APIRouter(prefix="/v1/admin", tags=["admin"])


class CreateAccessKeyRequest(BaseModel):
    label: str = ""
    usage_limit: int = Field(default=1, ge=1, le=1000)
    expires_at: datetime | None = None
    enabled: bool = True


class AccessKeyCreatedResponse(BaseModel):
    key: str
    record: dict[str, Any]


class AccessKeyPatchRequest(BaseModel):
    label: str | None = None
    usage_limit: int | None = Field(default=None, ge=1, le=1000)
    expires_at: datetime | None = None
    enabled: bool | None = None


class AdminSettingsPatch(BaseModel):
    model_base_url: str | None = None
    model_api_key: str | None = None
    model_name: str | None = None
    model_streaming_enabled: bool | None = None
    user_model_config_enabled: bool | None = None
    show_github_link: bool | None = None
    debug_console_enabled: bool | None = None
    debug_material_enabled: bool | None = None
    rag_status_user_visible: bool | None = None

    @field_validator("model_base_url")
    @classmethod
    def validate_model_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_openai_base_url(value)


class AdminModelConfigDraft(BaseModel):
    base_url: str | None = None
    api_key: SecretStr | None = None
    model: str | None = None

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        return normalize_openai_base_url(value)

    @field_validator("api_key", mode="before")
    @classmethod
    def empty_api_key_as_omitted(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @property
    def api_key_value(self) -> str | None:
        return self.api_key.get_secret_value() if self.api_key is not None else None


def require_admin_session(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthSessionRecord:
    record = get_current_admin_session(request, db)
    if record is None:
        raise HTTPException(status_code=401, detail="admin authentication required")
    return record


@router.post("/login", response_model=LoginResponse)
def admin_login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> LoginResponse:
    configured_password = settings.effective_admin_auth_password
    if not configured_password or not hmac.compare_digest(payload.password, configured_password):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")
    auth_session = create_auth_session(
        db,
        request,
        session_kind="admin",
        ttl_seconds=settings.admin_auth_session_ttl_seconds,
    )
    set_admin_auth_cookie(response, auth_session.session_id, auth_session.expires_at)
    return LoginResponse(
        expires_in=settings.admin_auth_session_ttl_seconds,
        history_namespace="admin",
    )


@router.get("/me", response_model=AuthStatusResponse)
def admin_me(
    request: Request,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    record = get_current_admin_session(request, db, touch=False)
    if record is None:
        return AuthStatusResponse(authenticated=False)
    return AuthStatusResponse(
        authenticated=True,
        expires_at=record.expires_at.isoformat() + "Z",
        history_namespace="admin",
    )


@router.post("/logout", response_model=AuthStatusResponse)
def admin_logout(
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
) -> AuthStatusResponse:
    revoke_current_admin_session(request, db)
    clear_admin_auth_cookie(response)
    return AuthStatusResponse(authenticated=False)


@router.post("/access-keys", response_model=AccessKeyCreatedResponse)
def create_access_key(
    payload: CreateAccessKeyRequest,
    admin_session: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> AccessKeyCreatedResponse:
    created = AccessKeyService(db).create_key(
        label=payload.label,
        usage_limit=payload.usage_limit,
        expires_at=payload.expires_at,
        enabled=payload.enabled,
        created_by_session_hash=admin_session.session_id_hash,
    )
    return AccessKeyCreatedResponse(
        key=created.plaintext_key,
        record=AccessKeyService.public_payload(created.record),
    )


@router.get("/access-keys")
def list_access_keys(
    q: str | None = None,
    status: Literal["enabled", "disabled", "all"] = "all",
    expired: bool | None = None,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return {
        "keys": AccessKeyService(db).list_keys(
            q=q,
            status=status,
            expired=expired,
        )
    }


@router.patch("/access-keys/{key_id}")
def update_access_key(
    key_id: str,
    payload: AccessKeyPatchRequest,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service = AccessKeyService(db)
    try:
        record = service.update_key(
            key_id=key_id,
            label=payload.label,
            usage_limit=payload.usage_limit,
            expires_at=payload.expires_at,
            expires_at_set="expires_at" in payload.model_fields_set,
            enabled=payload.enabled,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"record": AccessKeyService.public_payload(record)}


@router.get("/access-keys/{key_id}/secret")
def reveal_access_key_secret(
    key_id: str,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    try:
        revealed = AccessKeyService(db).reveal_key_secret(key_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload: dict[str, Any] = {
        "key_id": revealed.key_id,
        "key": revealed.key,
        "available": revealed.available,
    }
    if revealed.detail is not None:
        payload["detail"] = revealed.detail
    return payload


@router.get("/access-keys/{key_id}/sessions")
def list_key_sessions(
    key_id: str,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    rows = db.execute(
        select(AccessKeySessionRecord, SessionRecord)
        .join(SessionRecord, AccessKeySessionRecord.session_id == SessionRecord.session_id)
        .where(AccessKeySessionRecord.key_id == key_id)
        .order_by(AccessKeySessionRecord.created_at.desc())
    ).all()
    sessions = []
    for link, session in rows:
        turn_count = db.execute(
            select(SessionTurnRecord).where(SessionTurnRecord.session_id == session.session_id)
        ).scalars().all()
        sessions.append(
            {
                "session_id": session.session_id,
                "declared_family": session.declared_family,
                "phase_state": session.phase_state,
                "current_governor_decision": session.current_governor_decision,
                "created_at": link.created_at.isoformat() + "Z",
                "message_count": len(turn_count),
            }
        )
    return {"key_id": key_id, "sessions": sessions}


@router.get("/sessions/{session_id}/messages")
def get_admin_session_messages(
    session_id: str,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    session = db.get(SessionRecord, session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="session not found")
    turns = db.execute(
        select(SessionTurnRecord)
        .where(SessionTurnRecord.session_id == session_id)
        .order_by(SessionTurnRecord.turn_index, SessionTurnRecord.turn_id)
    ).scalars().all()
    return {
        "session_id": session_id,
        "declared_family": session.declared_family,
        "phase_state": session.phase_state,
        "messages": [
            {
                "turn_id": turn.turn_id,
                "turn_index": turn.turn_index,
                "role": turn.role,
                "content": turn.content,
                "source": turn.source,
                "client_message_id": turn.client_message_id,
                "metadata": turn.metadata_json,
            }
            for turn in turns
        ],
    }


@router.get("/settings")
def get_admin_settings(
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    return AdminConfigService(db).admin_payload()


@router.patch("/settings")
def update_admin_settings(
    payload: AdminSettingsPatch,
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    patch = payload.model_dump(exclude_unset=True)
    service = AdminConfigService(db)
    service.update_settings(patch)
    return service.admin_payload()


@router.post("/model-config/models")
def list_admin_model_config_models(
    payload: AdminModelConfigDraft | None = Body(default=None),
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    draft = payload or AdminModelConfigDraft()
    try:
        return AdminModelConfigService(db).list_models(
            base_url=draft.base_url,
            api_key=draft.api_key_value,
        )
    except ModelRuntimeError as exc:
        raise HTTPException(
            status_code=exc.status_code,
            detail=exc.to_public_payload(),
        ) from exc


@router.post("/model-config/test")
def test_admin_model_config(
    payload: AdminModelConfigDraft | None = Body(default=None),
    _: AuthSessionRecord = Depends(require_admin_session),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    draft = payload or AdminModelConfigDraft()
    return AdminModelConfigService(db).test_model(
        base_url=draft.base_url,
        api_key=draft.api_key_value,
        model=draft.model,
    )


@router.get("/rag/status")
def get_admin_rag_status(
    _: AuthSessionRecord = Depends(require_admin_session),
) -> dict[str, Any]:
    return PolicyKnowledgeIngestService().status_payload()
