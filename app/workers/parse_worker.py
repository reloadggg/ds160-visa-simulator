import asyncio
from collections.abc import Callable
import logging
import os

from fastapi import FastAPI
from app.db.models import JobRecord
from app.db.session import SessionLocal
from sqlalchemy.orm import Session

from app.repositories.document_repo import DocumentRepository
from app.services.document_pipeline import DocumentPipelineService
from app.services.gate_runtime_service import GateRuntimeService
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
        self.pipeline = DocumentPipelineService(db)
        self.recompute = ProfileRecomputeService(db)

    def run_once(self) -> bool:
        job = self.documents.claim_next_job("gate_parse")
        if job is None:
            return False

        job_id = job.job_id
        document_id = job.payload_json["document_id"]
        session_id = job.session_id
        self.db.commit()

        try:
            self.pipeline.process_document(document_id)
            self.recompute.recompute_session(session_id, save=False)
            completed_job = self.db.get(JobRecord, job_id)
            if completed_job is not None:
                completed_job.status = "completed"
            GateRuntimeService(self.db).refresh_session(session_id, save=False)
            self.db.commit()
            try:
                MessageService(self.db).refresh_after_material_change(
                    session_id,
                    reason=f"document_parsed:{document_id}",
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
        except Exception:
            self.db.rollback()
            failed_job = self.db.get(JobRecord, job_id)
            if failed_job is not None:
                failed_job.status = "failed"
            self.db.commit()
            raise


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
