from sqlalchemy.orm import Session

from app.integrations.parsers import extract_text
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class FileService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.repo = DocumentRepository(db)
        self.sessions = SessionRepository(db)

    def upload(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
    ) -> tuple[str, str]:
        if self.sessions.get(session_id) is None:
            raise SessionNotFoundError(session_id)

        text_preview = extract_text(filename, raw_bytes)
        try:
            document = self.repo.create_document(
                session_id=session_id,
                filename=filename,
                raw_bytes=raw_bytes,
                raw_text=text_preview,
            )
            job = self.repo.enqueue_job(
                session_id=session_id,
                kind="gate_parse",
                payload_json={
                    "document_id": document.document_id,
                    "text_preview": text_preview,
                },
            )
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return document.document_id, job.job_id
