from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.message_service import (
    MessageService,
    SessionClosedError,
    SessionNotFoundError,
)

router = APIRouter(prefix="/v1/sessions/{session_id}/messages", tags=["messages"])


class MessageRequest(BaseModel):
    role: Literal["user"]
    content: str


@router.post("")
def post_message(
    session_id: str,
    payload: MessageRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return MessageService(db).handle_user_turn(session_id, payload.content)
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
