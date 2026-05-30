import json
from collections.abc import Iterator
from queue import Empty, Queue
from threading import Thread
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, model_validator
from sqlalchemy.orm import Session

from app.agents.user_model_config import user_model_runtime
from app.core.settings import settings
from app.db.session import get_db, session_factory_from_session
from app.services.runtime_errors import ModelRuntimeError
from app.services.message_service import (
    DuplicateTurnInProgressError,
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
    client_message_id: str | None = None
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
            return MessageService(db).handle_user_turn(
                session_id,
                payload.content,
                client_message_id=payload.client_message_id,
            )
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SessionClosedError as exc:
        raise HTTPException(status_code=409, detail=exc.detail) from exc
    except DuplicateTurnInProgressError as exc:
        raise HTTPException(
            status_code=409,
            detail="这条消息正在处理中，请等待上一轮结果返回。",
        ) from exc
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
    try:
        runtime_config = to_runtime_config(payload.user_model_config)
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if runtime_config is not None and not settings.allow_user_model_streaming:
        raise HTTPException(status_code=403, detail="当前部署未启用用户模型流式输出。")
    stream_session_factory = session_factory_from_session(db)

    def event_stream() -> Iterator[str]:
        yield _sse_event("accepted", {"session_id": session_id})
        yield _sse_event(
            "debug_event",
            {
                "session_id": session_id,
                "phase": "message_turn",
                "step": "request_accepted",
                "status": "completed",
                "summary": "消息流请求已被后端接收。",
            },
        )
        yield _sse_event("analyzing", {"stage": "interview_runtime"})
        yield _sse_event(
            "debug_event",
            {
                "session_id": session_id,
                "phase": "message_turn",
                "step": "interview_runtime",
                "status": "started",
                "summary": "开始执行面谈运行时。",
            },
        )

        result_queue: Queue[tuple[str, dict]] = Queue()

        def run_message_turn() -> None:
            worker_db = stream_session_factory()
            try:
                result_queue.put(
                    (
                        "debug_event",
                        {
                            "session_id": session_id,
                            "phase": "message_turn",
                            "step": "message_service.handle_user_turn",
                            "status": "started",
                            "summary": "MessageService 已开始处理本轮用户消息。",
                        },
                    )
                )
                with user_model_runtime(runtime_config):
                    result = MessageService(worker_db).handle_user_turn(
                        session_id,
                        payload.content,
                        client_message_id=payload.client_message_id,
                    )
                result_queue.put(
                    (
                        "debug_event",
                        {
                            "session_id": session_id,
                            "phase": "message_turn",
                            "step": "message_service.handle_user_turn",
                            "status": "completed",
                            "summary": "MessageService 已完成本轮处理，准备返回最终响应。",
                            "payload": {
                                "governor_decision": result.get("governor_decision"),
                                "turn_decision": result.get("turn_decision", {}),
                            },
                        },
                    )
                )
                result_queue.put(("final", result))
            except SessionNotFoundError as exc:
                result_queue.put(("error", {"status": 404, "detail": str(exc)}))
            except SessionClosedError as exc:
                result_queue.put(("error", {"status": 409, "detail": exc.detail}))
            except DuplicateTurnInProgressError:
                result_queue.put(
                    (
                        "error",
                        {
                            "status": 409,
                            "detail": "这条消息正在处理中，请等待上一轮结果返回。",
                        },
                    )
                )
            except ModelRuntimeError as exc:
                result_queue.put(
                    ("error", {"status": exc.status_code, "detail": exc.detail})
                )
            except Exception as exc:
                result_queue.put(
                    (
                        "error",
                        {
                            "status": 500,
                            "detail": f"message stream failed: {exc}",
                        },
                    )
                )
            finally:
                worker_db.close()

        Thread(target=run_message_turn, daemon=True).start()

        while True:
            try:
                event, data = result_queue.get(timeout=15)
            except Empty:
                yield _sse_event(
                    "analyzing",
                    {
                        "stage": "interview_runtime",
                        "status": "still_running",
                    },
                )
                yield _sse_event(
                    "debug_event",
                    {
                        "session_id": session_id,
                        "phase": "message_turn",
                        "step": "interview_runtime",
                        "status": "still_running",
                        "summary": "后端仍在等待模型或运行时完成。",
                    },
                )
                continue

            yield _sse_event(event, data)
            if event in {"final", "error"}:
                return

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
