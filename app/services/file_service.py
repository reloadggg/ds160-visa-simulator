from sqlalchemy.orm import Session

from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.gate_runtime_service import GateRuntimeService


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
        session_record = self.sessions.get(session_id)
        if session_record is None:
            raise SessionNotFoundError(session_id)

        try:
            document = self.repo.create_document(
                session_id=session_id,
                filename=filename,
                raw_bytes=raw_bytes,
                raw_text="",
                artifact_json={"status": "uploaded", "filename": filename},
            )
            job = self.repo.enqueue_job(
                session_id=session_id,
                kind="gate_parse",
                payload_json={"document_id": document.document_id},
            )
            GateRuntimeService(self.db).refresh_record(session_record, save=False)
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return document.document_id, job.job_id
