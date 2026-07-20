import asyncio
from collections.abc import Callable
import logging
import os

from fastapi import FastAPI
from app.db.models import JobRecord
from app.db.session import SessionLocal
from sqlalchemy.orm import Session

from app.domain.case_memory import MaterialUnderstandingJob
from app.domain.evidence import DocumentAssessment, EvidenceItem
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.file_service import CASE_UNDERSTANDING_JOB_KIND
from app.services.document_pipeline import DocumentPipelineService
from app.services.gate_runtime_service import GateRuntimeService
from app.services.material_understanding_service import MaterialUnderstandingService
from app.services.message_service import MessageService
from app.services.profile_recompute_service import ProfileRecomputeService
from app.services.runtime_errors import ModelRuntimeError

logger = logging.getLogger(__name__)
DEFAULT_PARSE_WORKER_POLL_INTERVAL_SECONDS = 0.25
DEFAULT_PARSE_WORKER_SHUTDOWN_TIMEOUT_SECONDS = 1.0
ParseWorkerSessionFactory = Callable[[], Session]


class ParseWorker:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)
        self.pipeline = DocumentPipelineService(db)
        self.recompute = ProfileRecomputeService(db)
        self.material_understanding = MaterialUnderstandingService()
        self.case_memory = CaseMemoryService(db)

    def run_once(self) -> bool:
        job = self.documents.claim_next_job(CASE_UNDERSTANDING_JOB_KIND)
        if job is None:
            return False

        job_id = job.job_id
        document_id = job.payload_json["document_id"]
        session_id = job.session_id
        self.db.commit()

        try:
            if self._document_is_tombstoned(document_id):
                self._complete_job_as_cancelled(job_id)
                self.db.commit()
                return True

            process_result = self.pipeline.process_document(document_id)
            self.db.commit()

            if process_result.get("skipped_tombstoned") or self._document_is_tombstoned(
                document_id
            ):
                self._complete_job_as_cancelled(job_id)
                self.db.commit()
                return True

            document = self.documents.get_document(document_id)
            if document is None:
                raise LookupError(f"Document not found after processing: {document_id}")
            artifact = dict(document.artifact_json or {})
            understanding_job = self.material_understanding.understand(
                job_id=job_id,
                document_id=document_id,
                session_id=session_id,
                filename=document.filename,
                raw_bytes=document.raw_bytes,
                source_type=artifact.get("source_type", "unknown"),
                document_assessment=DocumentAssessment.from_artifact(artifact),
                legacy_evidence_items=[
                    self._evidence_item_from_record(record)
                    for record in self.evidence.list_document_evidence(document_id)
                ],
                case_memory=self.case_memory.build_board(session_id),
            )
            if self._document_is_tombstoned(document_id):
                self._complete_job_as_cancelled(job_id)
                self.db.commit()
                return True

            self.case_memory.upsert_material_understanding(
                document_id=document_id,
                job=understanding_job,
            )
            self.recompute.recompute_session(session_id, save=False)
            completed_job = self.db.get(JobRecord, job_id)
            if completed_job is not None:
                completed_job.status = understanding_job.status
            GateRuntimeService(self.db).refresh_session(session_id, save=False)
            self.db.commit()
            try:
                MessageService(self.db).refresh_after_material_change(
                    session_id,
                    reason=f"case_understanding:{document_id}",
                )
            except ModelRuntimeError:
                logger.warning(
                    "material change refresh skipped because turn model is unavailable",
                    extra={"session_id": session_id, "document_id": document_id},
                )
            except Exception:
                logger.exception(
                    "material change refresh failed after parse job completed",
                    extra={"session_id": session_id, "document_id": document_id},
                )
                self.db.rollback()
            return True
        except Exception as exc:
            self.db.rollback()
            if self._document_is_tombstoned(document_id):
                self._complete_job_as_cancelled(job_id)
                self.db.commit()
                return True
            failed_job = self.db.get(JobRecord, job_id)
            if failed_job is not None:
                failed_job.status = "failed"
            self._mark_material_understanding_failed(
                document_id=document_id,
                job_id=job_id,
                error=exc,
            )
            self.db.commit()
            raise

    def _document_is_tombstoned(self, document_id: str) -> bool:
        document = self.documents.get_document(document_id)
        return DocumentRepository.is_document_tombstoned(document)

    def _complete_job_as_cancelled(self, job_id: str) -> None:
        job = self.db.get(JobRecord, job_id)
        if job is not None and job.status not in {"completed", "failed", "cancelled"}:
            job.status = "cancelled"

    def _mark_material_understanding_failed(
        self,
        *,
        document_id: str,
        job_id: str,
        error: BaseException | None,
    ) -> None:
        document = self.documents.get_document(document_id)
        if document is None or DocumentRepository.is_document_tombstoned(document):
            return
        self.case_memory.upsert_material_understanding(
            document_id=document_id,
            job=MaterialUnderstandingJob(
                job_id=job_id,
                document_id=document_id,
                status="failed",
                error_code="parse_failed",
                error_message=self._failure_message(error),
            ),
        )

    def _failure_message(self, error: BaseException | None) -> str:
        if error is None:
            return "Document parsing failed before material understanding completed."
        detail = str(error).strip()
        if not detail:
            return f"{error.__class__.__name__} before material understanding."
        return (
            f"{error.__class__.__name__} before material understanding: "
            f"{detail[:240]}"
        )

    def _evidence_item_from_record(self, record) -> EvidenceItem:
        return EvidenceItem(
            evidence_id=record.evidence_id,
            session_id=record.session_id,
            document_id=record.document_id,
            chunk_id=record.chunk_id,
            evidence_type=record.evidence_type,
            field_path=record.field_path,
            value=record.value,
            excerpt=record.excerpt,
            confidence=record.confidence,
            metadata=record.metadata_json or {},
        )


def parse_worker_inline_enabled() -> bool:
    return os.getenv("PARSE_WORKER_INLINE", "").lower() in {"1", "true", "yes"}


def parse_worker_poll_interval_seconds() -> float:
    raw_value = os.getenv("PARSE_WORKER_POLL_INTERVAL_SECONDS")
    if raw_value is None:
        return DEFAULT_PARSE_WORKER_POLL_INTERVAL_SECONDS

    try:
        return max(float(raw_value), 0.01)
    except ValueError:
        return DEFAULT_PARSE_WORKER_POLL_INTERVAL_SECONDS


def parse_worker_shutdown_timeout_seconds() -> float:
    raw_value = os.getenv("PARSE_WORKER_SHUTDOWN_TIMEOUT_SECONDS")
    if raw_value is None:
        return DEFAULT_PARSE_WORKER_SHUTDOWN_TIMEOUT_SECONDS

    try:
        return max(float(raw_value), 0.01)
    except ValueError:
        return DEFAULT_PARSE_WORKER_SHUTDOWN_TIMEOUT_SECONDS


def drain_parse_jobs(session_factory: ParseWorkerSessionFactory) -> bool:
    processed_any_job = False
    with session_factory() as db:
        worker = ParseWorker(db)
        while worker.run_once():
            processed_any_job = True
    return processed_any_job


def resolve_parse_worker_session_factory(app: FastAPI) -> ParseWorkerSessionFactory:
    session_factory = getattr(app.state, "parse_worker_session_factory", None)
    if session_factory is not None:
        return session_factory
    return SessionLocal


async def _run_parse_worker_loop(
    session_factory: ParseWorkerSessionFactory,
    *,
    stop_event: asyncio.Event,
    poll_interval_seconds: float,
) -> None:
    while not stop_event.is_set():
        try:
            processed_any_job = await asyncio.to_thread(
                drain_parse_jobs,
                session_factory,
            )
        except Exception:
            logger.exception("parse worker loop iteration failed")
            processed_any_job = False

        if processed_any_job:
            await asyncio.sleep(0)
            continue

        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_seconds)
        except asyncio.TimeoutError:
            continue


async def start_parse_worker_runtime(app: FastAPI) -> None:
    if not parse_worker_inline_enabled():
        app.state.parse_worker_stop_event = None
        app.state.parse_worker_task = None
        return

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        _run_parse_worker_loop(
            resolve_parse_worker_session_factory(app),
            stop_event=stop_event,
            poll_interval_seconds=parse_worker_poll_interval_seconds(),
        )
    )
    app.state.parse_worker_stop_event = stop_event
    app.state.parse_worker_task = task


async def stop_parse_worker_runtime(app: FastAPI) -> None:
    stop_event = getattr(app.state, "parse_worker_stop_event", None)
    task = getattr(app.state, "parse_worker_task", None)
    if stop_event is None or task is None:
        app.state.parse_worker_stop_event = None
        app.state.parse_worker_task = None
        return

    stop_event.set()
    try:
        await asyncio.wait_for(
            asyncio.shield(task),
            timeout=parse_worker_shutdown_timeout_seconds(),
        )
    except asyncio.TimeoutError:
        logger.warning("parse worker runtime did not stop cleanly before timeout")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
    app.state.parse_worker_stop_event = None
    app.state.parse_worker_task = None
