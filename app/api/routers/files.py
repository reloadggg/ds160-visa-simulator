from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.file_service import FileService, FileTooLargeError, SessionNotFoundError
from app.services.file_service import UnsupportedFileTypeError

router = APIRouter(prefix="/v1/sessions/{session_id}/files", tags=["files"])


@router.post("", status_code=202)
async def upload_file(
    session_id: str,
    file: UploadFile = File(),
    document_type: str | None = Form(default=None),
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
        )
    except SessionNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except FileTooLargeError as exc:
        raise HTTPException(status_code=413, detail=str(exc)) from exc
    except UnsupportedFileTypeError as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    return {
        "document_id": result.document_id,
        "document_status": "uploaded",
        "job_id": result.job_id,
        "job_status": "queued",
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
        "requested_documents": list(result.requested_documents or []),
        "gate_progress": result.gate_progress,
    }
