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

        evidence_items = self.evidence.list_session_evidence(session_id)
        profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()

        funding_evidence = [
            item.evidence_id
            for item in evidence_items
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

        document_backed_fields = [
            ("/identity/full_name", "identity", "full_name"),
            ("/identity/passport_number", "identity", "passport_number"),
            ("/identity/nationality", "identity", "nationality"),
            ("/visa_intent/travel_purpose", "visa_intent", "travel_purpose"),
            ("/education/sevis_id", "education", "sevis_id"),
            ("/education/school_name", "education", "school_name"),
            ("/education/program_name", "education", "program_name"),
            ("/education/sponsor_name", "education", "sponsor_name"),
        ]
        for field_path, section, key in document_backed_fields:
            matching_items = [
                item
                for item in evidence_items
                if item.field_path == field_path and item.value
            ]
            if not matching_items:
                getattr(profile, section).pop(key, None)
                profile.field_states[field_path] = FieldStateRecord(
                    state=FieldState.UNKNOWN
                )
                profile.field_provenance[field_path] = FieldProvenanceRecord()
                continue
            best_item = max(matching_items, key=lambda item: item.confidence)
            getattr(profile, section)[key] = best_item.value
            profile.field_states[field_path] = FieldStateRecord(
                state=FieldState.DOCUMENTED
            )
            profile.field_provenance[field_path] = FieldProvenanceRecord(
                evidence_refs=[item.evidence_id for item in matching_items],
                source_summary="document evidence",
            )

        record.profile_json = profile.model_dump(mode="json")
        if save:
            self.sessions.save(record)
        else:
            self.db.add(record)
            self.db.flush()
        return profile
