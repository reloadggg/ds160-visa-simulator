from typing import Literal

import json
from collections.abc import Iterator

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.agents.user_model_config import user_model_runtime
from app.core.settings import settings
from app.db.session import get_db
from app.services.runtime_errors import ModelRuntimeError
from app.services.message_service import (
    MessageService,
    SessionClosedError,
    SessionNotFoundError,
)
from app.services.user_model_config_service import (
    UserModelConfigPayload,
    to_runtime_config,
)

router = APIRouter(prefix="/v1/sessions/{session_id}/messages", tags=["messages"])


class MessageRequest(BaseModel):
    role: Literal["user"]
    content: str
    user_model_config: UserModelConfigPayload | None = None

    @model_validator(mode="before")
    @classmethod
    def map_model_config_alias(cls, data):
        if isinstance(data, dict) and "model_config" in data:
            return {
                **data,
                "user_model_config": data.get("model_config"),
            }
        return data


@router.post("")
def post_message(
    session_id: str,
    payload: MessageRequest,
    db: Session = Depends(get_db),
) -> dict:
    try:
        runtime_config = to_runtime_config(payload.user_model_config)
        with user_model_runtime(runtime_config):
            return MessageService(db).handle_user_turn(session_id, payload.content)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except ModelRuntimeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from exc


def _sse_event(event: str, payload: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@router.post("/stream")
def stream_message(
    session_id: str,
    payload: MessageRequest,
    db: Session = Depends(get_db),
) -> StreamingResponse:
    if not settings.allow_user_model_streaming:
        raise HTTPException(status_code=403, detail="当前部署未启用用户模型流式输出。")
    try:
        runtime_config = to_runtime_config(payload.user_model_config)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    def event_stream() -> Iterator[str]:
        yield _sse_event("accepted", {"session_id": session_id})
        yield _sse_event("analyzing", {"stage": "interview_runtime"})
        try:
            with user_model_runtime(runtime_config):
                result = MessageService(db).handle_user_turn(session_id, payload.content)
        except SessionNotFoundError as exc:
            yield _sse_event("error", {"status": 404, "detail": str(exc)})
            return
        except SessionClosedError as exc:
            yield _sse_event("error", {"status": 409, "detail": exc.detail})
            return
        except ModelRuntimeError as exc:
            yield _sse_event(
                "error",
                {"status": exc.status_code, "detail": exc.detail},
            )
            return
        yield _sse_event("final", result)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
