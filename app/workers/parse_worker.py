from app.db.models import JobRecord
from sqlalchemy.orm import Session

from app.repositories.document_repo import DocumentRepository
from app.services.document_pipeline import DocumentPipelineService
from app.services.profile_recompute_service import ProfileRecomputeService


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

        try:
            document_id = job.payload_json["document_id"]
            self.pipeline.process_document(document_id)
            self.recompute.recompute_session(job.session_id)
            job.status = "completed"
            self.db.commit()
            return True
        except Exception:
            job_id = job.job_id
            self.db.rollback()
            failed_job = self.db.get(JobRecord, job_id)
            if failed_job is not None:
                failed_job.status = "failed"
            self.db.commit()
            raise
