from sqlalchemy.orm import Session

from app.integrations.parsers import extract_text
from app.repositories.document_repo import DocumentRepository


class FileService:
    def __init__(self, db: Session) -> None:
        self.repo = DocumentRepository(db)

    def upload(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
    ) -> tuple[str, str]:
        document = self.repo.create_document(session_id=session_id, filename=filename)
        text_preview = extract_text(filename, raw_bytes)
        job = self.repo.enqueue_job(
            session_id=session_id,
            kind="gate_parse",
            payload_json={
                "document_id": document.document_id,
                "text_preview": text_preview,
            },
        )
        return document.document_id, job.job_id
