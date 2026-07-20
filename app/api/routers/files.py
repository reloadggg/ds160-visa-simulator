from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.core.dependencies import require_session_access
from app.db.models import DocumentRecord, SessionRecord
from app.db.session import get_db
from app.domain.document_types import normalize_document_type
from app.domain.evidence import DocumentAssessment
from app.repositories.document_repo import DocumentRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.file_service import FileService, FileTooLargeError, FileUploadResult
from app.services.file_service import SessionNotFoundError, UnsupportedFileTypeError
from app.services.gate_runtime_service import GateRuntimeService
from app.services.profile_recompute_service import ProfileRecomputeService

router = APIRouter(prefix="/v1/sessions/{session_id}/files", tags=["files"])
documents_router = APIRouter(prefix="/v1/sessions/{session_id}/documents", tags=["files"])


def _session_is_terminal(record: SessionRecord) -> bool:
    return GateRuntimeService.is_terminal_session(record)


def _document_list_item(session_id: str, document: DocumentRecord) -> dict:
    artifact = dict(document.artifact_json or {})
    assessment = DocumentAssessment.from_artifact(artifact)
    metadata = artifact.get("metadata")
    metadata_document_type = (
        metadata.get("document_type") if isinstance(metadata, dict) else None
    )
    raw_document_type = (
        assessment.document_type
        or artifact.get("document_type")
        or metadata_document_type
    )
    document_type = normalize_document_type(raw_document_type) or raw_document_type
    if isinstance(document_type, str):
        document_type = document_type.strip() or None
    else:
        document_type = None
    understanding_status = artifact.get("understanding_status")
    if not isinstance(understanding_status, str) or not understanding_status.strip():
        understanding_status = None
    else:
        understanding_status = understanding_status.strip()
    case_board_delta = artifact.get("case_board_delta")
    if not isinstance(case_board_delta, dict):
        case_board_delta = None
    else:
        # Public list: keep a compact summary for restore/polling.
        latest_material = case_board_delta.get("latest_material")
        case_board_delta = {
            "latest_material": latest_material if isinstance(latest_material, dict) else None,
            "claim_count": len(case_board_delta.get("claims") or []),
            "evidence_card_count": len(case_board_delta.get("evidence_cards") or []),
        }
    uploaded_at = artifact.get("uploaded_at") or artifact.get("created_at")
    return {
        "document_id": document.document_id,
        "filename": document.filename,
        "status": document.status,
        "understanding_status": understanding_status,
        "document_type": document_type,
        "uploaded_at": uploaded_at,
        "content_url": f"/v1/sessions/{session_id}/files/{document.document_id}/content",
        "case_board_delta": case_board_delta,
        "tombstoned": DocumentRepository.is_document_tombstoned(document),
    }


def _list_session_documents_payload(session_id: str, db: Session) -> dict:
    documents = DocumentRepository(db).list_session_documents(session_id)
    items = [_document_list_item(session_id, document) for document in documents]
    return {
        "session_id": session_id,
        "documents": items,
        "count": len(items),
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


@router.get("")
def list_files(
    session_id: str,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict:
    return _list_session_documents_payload(session_id, db)


@documents_router.get("")
def list_documents(
    session_id: str,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict:
    """Public documents list for session restore / understanding polling."""
    return _list_session_documents_payload(session_id, db)


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
    document_repo = DocumentRepository(db)
    document = document_repo.get_document(document_id)
    if document is None or document.session_id != session_id:
        raise HTTPException(status_code=404, detail="document not found")

    document_repo.cancel_jobs_for_document(document_id)
    case_memory = CaseMemoryService(db)
    snapshot = case_memory.tombstone_document(
        document_id=document_id,
        reason="file_delete_api",
    )
    ProfileRecomputeService(db).recompute_session(session_id, save=False)
    snapshot = case_memory.rebuild_and_persist(session_id)
    GateRuntimeService(db).refresh_session(session_id, save=False)
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
