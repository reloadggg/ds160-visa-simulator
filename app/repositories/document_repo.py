from time import time_ns
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session, load_only

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
        artifact_json: dict | None = None,
    ) -> DocumentRecord:
        record = DocumentRecord(
            document_id=f"doc-{uuid4().hex[:12]}",
            session_id=session_id,
            filename=filename,
            raw_bytes=raw_bytes,
            raw_text=raw_text,
            artifact_json=artifact_json or {},
        )
        self.db.add(record)
        self.db.flush()
        return record

    def get_document(self, document_id: str) -> DocumentRecord | None:
        return self.db.get(DocumentRecord, document_id)

    def save_document(self, document: DocumentRecord) -> DocumentRecord:
        self.db.add(document)
        return document

    def list_session_documents(self, session_id: str) -> list[DocumentRecord]:
        return list(
            self.db.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.session_id == session_id)
                .options(
                    load_only(
                        DocumentRecord.document_id,
                        DocumentRecord.session_id,
                        DocumentRecord.filename,
                        DocumentRecord.status,
                        DocumentRecord.artifact_json,
                    )
                )
                .order_by(DocumentRecord.filename.asc(), DocumentRecord.document_id.asc())
            )
        )

    def list_session_document_exports(self, session_id: str) -> list[DocumentRecord]:
        return list(
            self.db.scalars(
                select(DocumentRecord)
                .where(DocumentRecord.session_id == session_id)
                .options(
                    load_only(
                        DocumentRecord.document_id,
                        DocumentRecord.session_id,
                        DocumentRecord.filename,
                        DocumentRecord.status,
                        DocumentRecord.artifact_json,
                        DocumentRecord.raw_text,
                    )
                )
                .order_by(DocumentRecord.filename.asc(), DocumentRecord.document_id.asc())
            )
        )

    def enqueue_job(
        self,
        session_id: str,
        kind: str,
        payload_json: dict,
    ) -> JobRecord:
        # 固定宽度时间前缀让 job_id 的字典序等价于入队顺序，
        # 这样 claim_next_job() 可继续按 job_id 升序取最早任务。
        job = JobRecord(
            job_id=f"job-{time_ns():020d}-{uuid4().hex[:6]}",
            session_id=session_id,
            kind=kind,
            payload_json=payload_json,
        )
        self.db.add(job)
        self.db.flush()
        return job

    def claim_next_job(self, kind: str) -> JobRecord | None:
        job = self.db.scalar(
            select(JobRecord)
            .where(
                JobRecord.kind == kind,
                JobRecord.status == "queued",
            )
            .order_by(JobRecord.job_id.asc()),
            )
        if job is None:
            return None

        job.status = "processing"
        self.db.flush()
        return job
