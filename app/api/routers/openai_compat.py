from typing import Literal

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.visa_families import validate_declared_family
from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.gate_service import GateService
from app.services.message_service import (
    DuplicateTurnInProgressError,
    MessageService,
    SessionNotFoundError,
)
from app.services.runtime_errors import ModelRuntimeError
from app.api.routers.openai_metadata import build_openai_adapter_metadata
from app.services.session_transcript_service import SessionTranscriptService

router = APIRouter(prefix="/v1/chat/completions", tags=["openai-compat"])


class CompatMessage(BaseModel):
    role: Literal["user", "assistant", "system"]
    content: str


class CompatRequest(BaseModel):
    model: str
    messages: list[CompatMessage] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)


@router.post("")
def chat_completions(
    payload: CompatRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> dict:
    last_user = _last_user_message(payload.messages)
    if last_user is None:
        raise HTTPException(status_code=422, detail="at least one user message is required")
    last_user_index, last_user_message = last_user
    session_repo = SessionRepository(db)
    transcript = SessionTranscriptService(db)
    turn_repo = SessionTurnRepository(db)
    metadata_client_message_id = _metadata_client_message_id(
        payload.metadata,
        transcript=transcript,
    )
    http_client_message_id = transcript.http_idempotency_client_message_id(
        endpoint="chat_completions",
        idempotency_key=idempotency_key,
        payload_fingerprint=transcript.request_payload_fingerprint(payload.messages),
    )
    session_id = payload.metadata.get("session_id")
    if session_id:
        session_record = session_repo.get(session_id)
        if session_record is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        context_mode = "existing_session"
    else:
        idempotent_turn = (
            turn_repo.find_any_user_turn_by_client_message_id(
                client_message_id=http_client_message_id,
            )
            if http_client_message_id
            else None
        )
        if idempotent_turn is not None:
            session_record = session_repo.get(idempotent_turn.session_id)
            if session_record is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"Session not found: {idempotent_turn.session_id}",
                )
            context_mode = "idempotency_replay"
        else:
            try:
                declared_family = validate_declared_family(
                    payload.metadata.get("declared_family")
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

            session_record = session_repo.create(
                declared_family=declared_family,
                gate_status_json=GateService().initial_gate_status(declared_family),
            )
            context_mode = "new_session"

    messages_to_import = payload.messages[:last_user_index]
    transcript.import_compat_messages(
        session_id=session_record.session_id,
        messages=messages_to_import,
        phase_state=session_record.phase_state,
    )
    client_message_id = (
        metadata_client_message_id
        or http_client_message_id
        or transcript.compat_request_client_message_id(
            session_id=session_record.session_id,
            messages=payload.messages,
            last_user_index=last_user_index,
            context_fingerprint=transcript.session_external_context_fingerprint(
                session_record
            ),
        )
    )

    try:
        result = MessageService(db).handle_user_turn(
            session_record.session_id,
            last_user_message,
            client_message_id=client_message_id,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except DuplicateTurnInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail="这条消息正在处理中，请等待上一轮结果返回。",
        ) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc

    session_record = session_repo.get(session_record.session_id) or session_record
    return {
        "id": f"chatcmpl-{session_record.session_id}",
        "object": "chat.completion",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result["assistant_message"]},
                "finish_reason": "stop",
            }
        ],
        "metadata": build_openai_adapter_metadata(
            db,
            session_record=session_record,
            result=result,
            context_mode=context_mode,
        ),
    }


def _last_user_message(messages: list[CompatMessage]) -> tuple[int, str] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message.role == "user":
            return index, message.content
    return None


def _metadata_client_message_id(
    metadata: dict,
    *,
    transcript: SessionTranscriptService,
) -> str | None:
    for key in ("client_message_id", "idempotency_key"):
        value = metadata.get(key)
        normalized = transcript.normalize_client_message_id(value)
        if normalized:
            return normalized
    return None
