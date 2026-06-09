from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from sqlalchemy.orm import Session

from app.api.routers.files import (
    _case_board_refresh_payload,
    _session_is_terminal,
)
from app.core.dependencies import current_access_key_id, require_session_access
from app.db.models import SessionRecord
from app.db.session import get_db
from app.services.file_service import (
    FileService,
    FileTooLargeError,
    FileUploadResult,
    SessionNotFoundError,
    UnsupportedFileTypeError,
)
from app.services.wx_upload_ticket_service import (
    WxUploadTicketError,
    WxUploadTicketService,
)


session_router = APIRouter(prefix="/v1/sessions/{session_id}", tags=["wx-upload"])
ticket_router = APIRouter(prefix="/v1/wx/upload-tickets", tags=["wx-upload"])


def _file_upload_payload(session_id: str, result: FileUploadResult) -> dict[str, Any]:
    return {
        "document_id": result.document_id,
        "content_url": f"/v1/sessions/{session_id}/files/{result.document_id}/content",
        "document_status": "uploaded",
        "job_id": result.job_id,
        "job_status": "queued",
        "understanding_status": result.understanding_status,
        "document_type": result.document_type,
        "document_assessment": (
            None
            if result.document_assessment is None
            else result.document_assessment.to_metadata_payload()
        ),
        "document_type_candidates": list(result.document_type_candidates or []),
        "relevance": result.relevance,
        "supported_claims": list(result.supported_claims or []),
        "confidence": result.confidence,
        "feedback_message": result.feedback_message,
        "relevant": result.relevant,
        "main_flow_feedback": result.main_flow_feedback,
        "case_board_delta": result.case_board_delta,
        "case_board_refresh": _case_board_refresh_payload(session_id, result),
        "evidence_cards": list(result.evidence_cards or []),
        "requested_documents": list(result.requested_documents or []),
        "remaining_required_documents": list(
            result.remaining_required_documents or []
        ),
        "gate_progress": result.gate_progress,
    }


def _raise_ticket_error(exc: WxUploadTicketError) -> None:
    raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc


@session_router.post("/upload-ticket")
def create_upload_ticket(
    session_id: str,
    request: Request,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    access_key_id = current_access_key_id(request, db)
    try:
        created = WxUploadTicketService(db).create_ticket(
            session_id=session_id,
            access_key_id=access_key_id,
        )
    except WxUploadTicketError as exc:
        _raise_ticket_error(exc)
    return WxUploadTicketService(db).status_payload(
        ticket=created.ticket,
        record=created.record,
    )


@ticket_router.get("/{ticket}")
def get_upload_ticket_status(
    ticket: str,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service = WxUploadTicketService(db)
    try:
        record = service.require_record(ticket)
    except WxUploadTicketError as exc:
        _raise_ticket_error(exc)
    return service.status_payload(ticket=ticket, record=record)


@ticket_router.post("/{ticket}/files", status_code=202)
async def upload_file_with_ticket(
    ticket: str,
    file: UploadFile = File(),
    session_id: str | None = Form(default=None),
    document_type: str | None = Form(default=None),
    context_text: str | None = Form(default=None),
    original_name: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    service = WxUploadTicketService(db)
    try:
        ticket_record = service.validate_for_upload(ticket)
    except WxUploadTicketError as exc:
        _raise_ticket_error(exc)

    if session_id and session_id != ticket_record.session_id:
        raise HTTPException(
            status_code=403,
            detail="upload ticket does not match session",
        )

    record = db.get(SessionRecord, ticket_record.session_id)
    if record is not None and _session_is_terminal(record):
        raise HTTPException(status_code=409, detail="本轮面签已结束，不能继续上传材料。")

    raw_bytes = await file.read()
    filename = original_name or file.filename or "wechat-upload"
    try:
        result = FileService(db).upload(
            ticket_record.session_id,
            filename,
            raw_bytes,
            file.content_type,
            document_type,
            context_text,
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc

    upload_payload = _file_upload_payload(ticket_record.session_id, result)
    ticket_record = service.record_upload_result(
        ticket_record,
        result_payload=upload_payload,
        filename=filename,
        content_type=file.content_type,
        size=len(raw_bytes),
    )
    return {
        **service.status_payload(ticket=ticket, record=ticket_record),
        "upload": upload_payload,
    }
