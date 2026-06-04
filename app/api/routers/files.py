from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import require_session_access
from app.db.models import SessionRecord
from app.db.session import get_db
from app.repositories.document_repo import DocumentRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.file_service import FileService, FileTooLargeError, FileUploadResult
from app.services.file_service import SessionNotFoundError, UnsupportedFileTypeError

router = APIRouter(prefix="/v1/sessions/{session_id}/files", tags=["files"])


def _session_is_terminal(record: SessionRecord) -> bool:
    if record.phase_state in {"completed", "session_closed"}:
        return True
    if record.current_governor_decision in {
        "simulated_refusal",
        "passed",
        "not_passed",
        "refused",
    }:
        return True
    interviewer_state = record.interviewer_state_json or {}
    return interviewer_state.get("status") in {
        "simulated_refusal",
        "passed",
        "not_passed",
        "refused",
        "completed",
    }


def _case_board_refresh_payload(
    session_id: str,
    result: FileUploadResult,
) -> dict:
    latest_material = (
        result.case_board_delta.get("latest_material")
        if isinstance(result.case_board_delta, dict)
        else {}
    ) or {}
    understanding_error = latest_material.get("understanding_error")
    if not isinstance(understanding_error, dict):
        understanding_error = {}
    failure_message = understanding_error.get("message")
    failure_node = understanding_error.get("code")
    if (
        result.understanding_status in {"failed", "error"}
        and not failure_message
        and latest_material.get("unknowns")
    ):
        unknowns = latest_material.get("unknowns")
        if isinstance(unknowns, list) and unknowns:
            failure_message = unknowns[0]

    return {
        "event_type": "material_uploaded",
        "document_id": result.document_id,
        "status": "queued",
        "understanding_status": result.understanding_status,
        "failure_node": failure_node,
        "failure_message": failure_message,
        "debug_timeline_scope": {
            "session_id": session_id,
            "document_id": result.document_id,
            "scope": "material_understanding",
        },
        "message_policy": "case_board_timeline_only",
    }


@router.post("", status_code=202)
async def upload_file(
    session_id: str,
    file: UploadFile = File(),
    document_type: str | None = Form(default=None),
    context_text: str | None = Form(default=None),
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict:
    record = db.get(SessionRecord, session_id)
    if record is not None and _session_is_terminal(record):
        raise HTTPException(status_code=409, detail="本轮面签已结束，不能继续上传材料。")
    raw_bytes = await file.read()
    try:
        result = FileService(db).upload(
            session_id,
            file.filename,
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


@router.get("/{document_id}/content")
def get_file_content(
    session_id: str,
    document_id: str,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> Response:
    document = DocumentRepository(db).get_document(document_id)
    if document is None or document.session_id != session_id:
        raise HTTPException(status_code=404, detail="document not found")
    content_type = document.artifact_json.get("content_type") or "application/octet-stream"
    return Response(
        content=document.raw_bytes or b"",
        media_type=content_type,
        headers={"Content-Disposition": f'inline; filename="{document.filename}"'},
    )


@router.delete("/{document_id}", status_code=200)
def delete_file(
    session_id: str,
    document_id: str,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict:
    document = DocumentRepository(db).get_document(document_id)
    if document is None or document.session_id != session_id:
        raise HTTPException(status_code=404, detail="document not found")

    snapshot = CaseMemoryService(db).tombstone_document(
        document_id=document_id,
        reason="file_delete_api",
    )
    db.commit()
    return {
        "document_id": document_id,
        "document_status": "tombstoned",
        "case_board": {
            "schema_version": "case_board.v1",
            "claims": [item.model_dump(mode="json") for item in snapshot.claims],
            "evidence_cards": [
                item.model_dump(mode="json") for item in snapshot.evidence_cards
            ],
            "proof_points": [
                item.model_dump(mode="json") for item in snapshot.proof_points
            ],
            "conflicts": [
                item.model_dump(mode="json") for item in snapshot.conflicts
            ],
        },
    }
