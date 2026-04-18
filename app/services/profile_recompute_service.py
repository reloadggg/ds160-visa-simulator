from sqlalchemy.orm import Session

from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.repositories.evidence_repo import EvidenceRepository
from app.repositories.session_repo import SessionRepository

class ProfileRecomputeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.evidence = EvidenceRepository(db)
        self.sessions = SessionRepository(db)

    def recompute_session(
        self,
        session_id: str,
        *,
        save: bool = True,
    ) -> ApplicantProfile:
        record = self.sessions.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")

        if record.profile_json:
            profile = ApplicantProfile.model_validate(record.profile_json)
        else:
            profile = ApplicantProfile.minimal(profile_id=f"profile-{session_id}")

        profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()

        funding_evidence = [
            item.evidence_id
            for item in self.evidence.list_session_evidence(session_id)
            if item.field_path == "/funding/primary_source" and item.value == "parents"
        ]

        if funding_evidence:
            profile.funding["primary_source"] = "parents"
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.DOCUMENTED
            )
            profile.field_provenance["/funding/primary_source"] = (
                FieldProvenanceRecord(
                    evidence_refs=funding_evidence,
                    source_summary="document evidence",
                )
            )
        elif profile.funding.get("primary_source") == "parents":
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.CLAIMED
            )
        else:
            profile.field_states["/funding/primary_source"] = FieldStateRecord(
                state=FieldState.UNKNOWN
            )

        record.profile_json = profile.model_dump(mode="json")
        if save:
            self.sessions.save(record)
        else:
            self.db.add(record)
            self.db.flush()
        return profile
