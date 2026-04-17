from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
from app.services.message_service import MessageService

router = APIRouter(prefix="/v1/chat/completions", tags=["openai-compat"])


class CompatMessage(BaseModel):
    role: str
    content: str


class CompatRequest(BaseModel):
    model: str
    messages: list[CompatMessage]
    metadata: dict = Field(default_factory=dict)


@router.post("")
def chat_completions(
    payload: CompatRequest,
    db: Session = Depends(get_db),
) -> dict:
    declared_family = payload.metadata.get("declared_family")
    session_record = SessionRepository(db).create(declared_family)
    last_user_message = payload.messages[-1].content
    result = MessageService(db).handle_user_turn(session_record.session_id, last_user_message)
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
            "phase_state": "intake",
        },
    }
