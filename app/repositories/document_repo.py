from time import time_ns
from uuid import uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session, load_only

from app.db.models import DocumentRecord, JobRecord

_ACTIVE_JOB_STATUSES = ("queued", "processing")
_CLAIM_SCAN_LIMIT = 32


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

    @staticmethod
    def is_document_tombstoned(document: DocumentRecord | None) -> bool:
        if document is None:
            return False
        if document.status in {"deleted", "tombstoned"}:
            return True
        artifact = dict(document.artifact_json or {})
        tombstone = artifact.get("case_memory_tombstone")
        return isinstance(tombstone, dict) and tombstone.get("status") == "tombstoned"

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

    def cancel_jobs_for_document(
        self,
        document_id: str,
        *,
        statuses: tuple[str, ...] = _ACTIVE_JOB_STATUSES,
    ) -> int:
        jobs = list(
            self.db.scalars(
                select(JobRecord).where(JobRecord.status.in_(statuses))
            )
        )
        cancelled = 0
        for job in jobs:
            payload = job.payload_json or {}
            if payload.get("document_id") != document_id:
                continue
            job.status = "cancelled"
            cancelled += 1
        if cancelled:
            self.db.flush()
        return cancelled

    def cancel_jobs_for_session(
        self,
        session_id: str,
        *,
        statuses: tuple[str, ...] = _ACTIVE_JOB_STATUSES,
    ) -> int:
        jobs = list(
            self.db.scalars(
                select(JobRecord).where(
                    JobRecord.session_id == session_id,
                    JobRecord.status.in_(statuses),
                )
            )
        )
        for job in jobs:
            job.status = "cancelled"
        if jobs:
            self.db.flush()
        return len(jobs)

    def cancel_jobs_for_documents(
        self,
        document_ids: list[str],
        *,
        statuses: tuple[str, ...] = _ACTIVE_JOB_STATUSES,
    ) -> int:
        target_ids = {item for item in document_ids if item}
        if not target_ids:
            return 0
        jobs = list(
            self.db.scalars(
                select(JobRecord).where(JobRecord.status.in_(statuses))
            )
        )
        cancelled = 0
        for job in jobs:
            payload = job.payload_json or {}
            if payload.get("document_id") not in target_ids:
                continue
            job.status = "cancelled"
            cancelled += 1
        if cancelled:
            self.db.flush()
        return cancelled

    def claim_next_job(self, kind: str) -> JobRecord | None:
        """Claim the oldest queued job whose document is not tombstoned.

        Uses best-effort row locking (``FOR UPDATE SKIP LOCKED`` when the
        dialect supports it) and an atomic status transition so concurrent
        workers do not double-claim. Jobs for tombstoned documents are
        cancelled and skipped.
        """
        statement = (
            select(JobRecord)
            .where(
                JobRecord.kind == kind,
                JobRecord.status == "queued",
            )
            .order_by(JobRecord.job_id.asc())
            .limit(_CLAIM_SCAN_LIMIT)
        )
        try:
            statement = statement.with_for_update(skip_locked=True)
        except Exception:
            # Dialects without SKIP LOCKED (e.g. some SQLite builds) fall back
            # to the plain ordered select; atomic UPDATE still prevents double-claim.
            pass

        candidates = list(self.db.scalars(statement))
        for job in candidates:
            document_id = (job.payload_json or {}).get("document_id")
            if document_id:
                document = self.get_document(str(document_id))
                if self.is_document_tombstoned(document):
                    job.status = "cancelled"
                    self.db.flush()
                    continue

            result = self.db.execute(
                update(JobRecord)
                .where(
                    JobRecord.job_id == job.job_id,
                    JobRecord.status == "queued",
                )
                .values(status="processing")
            )
            if result.rowcount != 1:
                continue
            self.db.flush()
            self.db.refresh(job)
            return job
        return None
