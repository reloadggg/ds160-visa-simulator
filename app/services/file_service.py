from sqlalchemy.orm import Session

from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
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
        session_record = self.sessions.get(session_id)
        if session_record is None:
            raise SessionNotFoundError(session_id)

        text_preview = extract_text(filename, raw_bytes)
        profile = self._load_profile(session_id, session_record.profile_json)
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
            self._apply_document_evidence(
                profile=profile,
                filename=filename,
                text_preview=text_preview,
                document_id=document.document_id,
            )
            session_record.profile_json = profile.model_dump(mode="json")
            self.db.commit()
        except Exception:
            self.db.rollback()
            raise
        return document.document_id, job.job_id

    def _load_profile(self, session_id: str, profile_json: dict) -> ApplicantProfile:
        if profile_json:
            return ApplicantProfile.model_validate(profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{session_id}")

    def _apply_document_evidence(
        self,
        profile: ApplicantProfile,
        filename: str,
        text_preview: str,
        document_id: str,
    ) -> None:
        normalized = f"{filename}\n{text_preview}".lower()
        is_funding_evidence = any(
            token in normalized
            for token in ("funding", "bank", "sponsor", "balance", "statement")
        )
        if not is_funding_evidence:
            return

        provenance = profile.field_provenance.setdefault(
            "/funding/primary_source",
            FieldProvenanceRecord(),
        )
        if document_id not in provenance.evidence_refs:
            provenance.evidence_refs.append(document_id)

        if "parent" in normalized or profile.funding.get("primary_source") == "parents":
            profile.funding["primary_source"] = "parents"

        profile.field_states["/funding/primary_source"] = FieldStateRecord(
            state=FieldState.DOCUMENTED,
        )
