from fastapi import APIRouter, Depends, File, UploadFile
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.file_service import FileService

router = APIRouter(prefix="/v1/sessions/{session_id}/files", tags=["files"])


@router.post("", status_code=202)
async def upload_file(
    session_id: str,
    file: UploadFile = File(),
    db: Session = Depends(get_db),
) -> dict:
    raw_bytes = await file.read()
    document_id, job_id = FileService(db).upload(session_id, file.filename, raw_bytes)
    return {
        "document_id": document_id,
        "document_status": "uploaded",
        "job_id": job_id,
        "job_status": "queued",
    }
