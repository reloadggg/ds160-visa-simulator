from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import DocumentRecord, JobRecord


class DocumentRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_document(
        self,
        session_id: str,
        filename: str,
        raw_bytes: bytes,
        raw_text: str,
    ) -> DocumentRecord:
        record = DocumentRecord(
            document_id=f"doc-{uuid4().hex[:12]}",
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
        )
        self.db.add(record)
        self.db.flush()
        return record

    def enqueue_job(
        self,
        session_id: str,
        kind: str,
        payload_json: dict,
    ) -> JobRecord:
        job = JobRecord(
            job_id=f"job-{uuid4().hex[:12]}",
            session_id=session_id,
            kind=kind,
            payload_json=payload_json,
        )
        self.db.add(job)
        self.db.flush()
        return job
