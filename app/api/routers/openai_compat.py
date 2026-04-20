from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.core.visa_families import validate_declared_family
from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
from app.services.gate_service import GateService
from app.services.message_service import MessageService, SessionNotFoundError

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
    db: Session = Depends(get_db),
) -> dict:
    last_user_message = next(
        (message.content for message in reversed(payload.messages) if message.role == "user"),
        None,
    )
    if last_user_message is None:
        raise HTTPException(status_code=422, detail="at least one user message is required")
    session_repo = SessionRepository(db)
    session_id = payload.metadata.get("session_id")
    if session_id:
        session_record = session_repo.get(session_id)
        if session_record is None:
            raise HTTPException(status_code=404, detail=f"Session not found: {session_id}")
        context_mode = "existing_session"
    else:
        try:
            declared_family = validate_declared_family(payload.metadata.get("declared_family"))
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        session_record = session_repo.create(
            declared_family=declared_family,
            gate_status_json=GateService().initial_gate_status(declared_family),
        )
        context_mode = "new_session"

    try:
        result = MessageService(db).handle_user_turn(session_record.session_id, last_user_message)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc

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
        "metadata": {
            "session_id": session_record.session_id,
            "phase_state": session_record.phase_state,
            "context_mode": context_mode,
        },
    }
