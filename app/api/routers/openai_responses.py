from __future__ import annotations

from dataclasses import dataclass
from time import time
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.visa_families import validate_declared_family
from app.db.models import SessionTurnRecord
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

router = APIRouter(prefix="/v1/responses", tags=["openai-responses"])


class ResponsesRequest(BaseModel):
    model: str
    input: str | list[Any] = Field(min_length=1)
    instructions: str | None = None
    previous_response_id: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@router.post("")
def create_response(
    payload: ResponsesRequest,
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    input_messages = _input_messages(payload.input)
    if payload.instructions:
        input_messages.insert(0, {"role": "system", "content": payload.instructions})

    last_user = _last_user_message(input_messages)
    if last_user is None:
        raise HTTPException(status_code=422, detail="at least one user input is required")
    last_user_index, last_user_message = last_user

    session_repo = SessionRepository(db)
    turn_repo = SessionTurnRepository(db)
    transcript = SessionTranscriptService(db)
    previous_response = _response_reference(payload.previous_response_id)
    previous_session_id = (
        previous_response.session_id if previous_response is not None else None
    )
    metadata_session_id = _metadata_session_id(payload.metadata)
    if previous_session_id and metadata_session_id and previous_session_id != metadata_session_id:
        raise HTTPException(
            status_code=422,
            detail="metadata.session_id does not match previous_response_id",
        )
    metadata_client_message_id = _metadata_client_message_id(
        payload.metadata,
        transcript=transcript,
    )
    http_client_message_id = transcript.http_idempotency_client_message_id(
        endpoint="responses",
        idempotency_key=idempotency_key,
        payload_fingerprint=(
            f"{payload.previous_response_id or ''}:"
            f"{transcript.request_payload_fingerprint(input_messages)}"
        ),
    )
    session_id = metadata_session_id or previous_session_id

    if session_id:
        session_record = session_repo.get(session_id)
        if session_record is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        if previous_response is not None and turn_repo.assistant_turn_at_index(
            session_id=previous_response.session_id,
            turn_index=previous_response.turn_index,
        ) is None:
            raise HTTPException(
                status_code=404,
                detail=f"previous_response_id not found: {payload.previous_response_id}",
            )
        context_mode = (
            "previous_response" if previous_session_id else "existing_session"
        )
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

    transcript.import_compat_messages(
        session_id=session_record.session_id,
        messages=input_messages[:last_user_index],
        phase_state=session_record.phase_state,
    )
    client_message_id = (
        metadata_client_message_id
        or http_client_message_id
        or transcript.responses_request_client_message_id(
            session_id=session_record.session_id,
            messages=input_messages,
            last_user_index=last_user_index,
            previous_response_id=payload.previous_response_id,
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
    assistant_turn = _latest_assistant_turn(db, session_record.session_id)
    response_id = _response_id(session_record.session_id, assistant_turn)
    output_text = result["assistant_message"]
    return {
        "id": response_id,
        "object": "response",
        "created_at": int(time()),
        "status": "completed",
        "model": payload.model,
        "output": [
            {
                "id": f"msg-{response_id}",
                "type": "message",
                "status": "completed",
                "role": "assistant",
                "content": [
                    {
                        "type": "output_text",
                        "text": output_text,
                        "annotations": [],
                    }
                ],
            }
        ],
        "output_text": output_text,
        "metadata": build_openai_adapter_metadata(
            db,
            session_record=session_record,
            result=result,
            context_mode=context_mode,
        ),
    }


def _input_messages(input_payload: str | list[Any]) -> list[dict[str, str]]:
    if isinstance(input_payload, str):
        return [{"role": "user", "content": input_payload}]

    messages: list[dict[str, str]] = []
    for item in input_payload:
        if isinstance(item, str):
            messages.append({"role": "user", "content": item})
            continue
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        if role not in {"user", "assistant", "system"}:
            continue
        content = _content_text(item.get("content"))
        if content:
            messages.append({"role": role, "content": content})
    return messages


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "\n".join(part for part in parts if part).strip()


def _last_user_message(messages: list[dict[str, str]]) -> tuple[int, str] | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if message["role"] == "user":
            return index, message["content"]
    return None


def _metadata_session_id(metadata: dict[str, Any]) -> str | None:
    value = metadata.get("session_id")
    return value.strip() if isinstance(value, str) and value.strip() else None


def _metadata_client_message_id(
    metadata: dict[str, Any],
    *,
    transcript: SessionTranscriptService,
) -> str | None:
    for key in ("client_message_id", "idempotency_key"):
        value = metadata.get(key)
        normalized = transcript.normalize_client_message_id(value)
        if normalized:
            return normalized
    return None


@dataclass(frozen=True)
class ResponseReference:
    session_id: str
    turn_index: int


def _response_reference(response_id: str | None) -> ResponseReference | None:
    if response_id is None:
        return None
    if not isinstance(response_id, str) or not response_id.startswith("resp-"):
        raise HTTPException(status_code=422, detail="invalid previous_response_id")
    rest = response_id.removeprefix("resp-")
    session_id, separator, turn_index = rest.rpartition("-")
    if not separator or not session_id.startswith("sess-") or not turn_index.isdigit():
        raise HTTPException(status_code=422, detail="invalid previous_response_id")
    return ResponseReference(session_id=session_id, turn_index=int(turn_index))


def _latest_assistant_turn(
    db: Session,
    session_id: str,
) -> SessionTurnRecord | None:
    turns = SessionTurnRepository(db).list_session_turns(session_id)
    for turn in reversed(turns):
        if turn.role == "assistant":
            return turn
    return None


def _response_id(
    session_id: str,
    assistant_turn: SessionTurnRecord | None,
) -> str:
    turn_index = assistant_turn.turn_index if assistant_turn is not None else 0
    return f"resp-{session_id}-{turn_index}"
