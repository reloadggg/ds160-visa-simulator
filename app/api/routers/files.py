from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.document_repo import DocumentRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.file_service import FileService, FileTooLargeError, SessionNotFoundError
from app.services.file_service import UnsupportedFileTypeError

router = APIRouter(prefix="/v1/sessions/{session_id}/files", tags=["files"])


@router.post("", status_code=202)
async def upload_file(
    session_id: str,
    file: UploadFile = File(),
    document_type: str | None = Form(default=None),
    context_text: str | None = Form(default=None),
    db: Session = Depends(get_db),
) -> dict:
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
