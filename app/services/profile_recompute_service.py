from sqlalchemy.orm import Session

from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.repositories.session_repo import SessionRepository
from app.services.evidence_service import EvidenceService, SessionFieldEvidenceSummary


_DOCUMENT_BACKED_FIELDS: list[tuple[str, str, str]] = [
    ("/funding/primary_source", "funding", "primary_source"),
    ("/identity/full_name", "identity", "full_name"),
    ("/identity/passport_number", "identity", "passport_number"),
    ("/identity/nationality", "identity", "nationality"),
    ("/visa_intent/travel_purpose", "visa_intent", "travel_purpose"),
    ("/education/sevis_id", "education", "sevis_id"),
    ("/education/school_name", "education", "school_name"),
    ("/education/program_name", "education", "program_name"),
    ("/education/sponsor_name", "education", "sponsor_name"),
    ("/education/first_year_cost", "education", "first_year_cost"),
    ("/funding/available_funds", "funding", "available_funds"),
    ("/funding/source_detail", "funding", "source_detail"),
    ("/funding/equity_ownership", "funding", "equity_ownership"),
    ("/funding/sponsor_relationship", "funding", "sponsor_relationship"),
    ("/family/parent_names", "family_specific", "parent_names"),
]


class ProfileRecomputeService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.evidence = EvidenceService(db)
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

        field_summaries = self.evidence.summarize_session_field_evidence(session_id)
        for field_path, section, key in _DOCUMENT_BACKED_FIELDS:
            self._apply_field_evidence(
                profile=profile,
                section=section,
                key=key,
                field_path=field_path,
                summary=field_summaries.get(field_path),
            )

        record.profile_json = profile.model_dump(mode="json")
        if save:
            self.sessions.save(record)
        else:
            self.db.add(record)
        return profile

    def _apply_field_evidence(
        self,
        *,
        profile: ApplicantProfile,
        section: str,
        key: str,
        field_path: str,
        summary: SessionFieldEvidenceSummary | None,
    ) -> None:
        container = getattr(profile, section)
        existing_value = container.get(key)
        existing_state = profile.field_states.get(field_path)

        if summary is None:
            if existing_state is not None and existing_state.state == FieldState.CLAIMED and existing_value:
                profile.field_states[field_path] = FieldStateRecord(
                    state=FieldState.CLAIMED
                )
                profile.field_provenance[field_path] = FieldProvenanceRecord()
                return

            container.pop(key, None)
            profile.field_states[field_path] = FieldStateRecord(state=FieldState.UNKNOWN)
            profile.field_provenance[field_path] = FieldProvenanceRecord()
            self._clear_document_snapshot(profile, field_path)
            return

        normalized_existing = self._normalize_value(str(existing_value)) if existing_value else None

        if summary.has_conflict:
            if existing_state is not None and existing_state.state == FieldState.CLAIMED and existing_value:
                self._remember_claimed_value(profile, field_path, str(existing_value))
            container.pop(key, None)
            profile.field_states[field_path] = FieldStateRecord(
                state=FieldState.CONFLICTED
            )
            profile.field_provenance[field_path] = FieldProvenanceRecord(
                evidence_refs=summary.evidence_refs,
                source_summary="conflicting document evidence",
            )
            self._record_document_snapshot(
                profile,
                field_path,
                value=str(summary.best_value) if summary.best_value is not None else None,
                state=FieldState.CONFLICTED,
                evidence_refs=summary.evidence_refs,
            )
            return

        if (
            existing_state is not None
            and existing_state.state == FieldState.CLAIMED
            and existing_value
            and normalized_existing != self._normalize_value(str(summary.best_value))
        ):
            self._remember_claimed_value(profile, field_path, str(existing_value))
            container.pop(key, None)
            profile.field_states[field_path] = FieldStateRecord(
                state=FieldState.CONFLICTED
            )
            profile.field_provenance[field_path] = FieldProvenanceRecord(
                evidence_refs=summary.evidence_refs,
                source_summary="document evidence conflicts with claimed value",
            )
            self._record_document_snapshot(
                profile,
                field_path,
                value=str(summary.best_value) if summary.best_value is not None else None,
                state=FieldState.DOCUMENTED,
                evidence_refs=summary.evidence_refs,
            )
            return

        container[key] = summary.best_value
        profile.field_states[field_path] = FieldStateRecord(
            state=FieldState.DOCUMENTED
        )
        profile.field_provenance[field_path] = FieldProvenanceRecord(
            evidence_refs=summary.evidence_refs,
            source_summary="document evidence",
        )
        self._record_document_snapshot(
            profile,
            field_path,
            value=str(summary.best_value) if summary.best_value is not None else None,
            state=FieldState.DOCUMENTED,
            evidence_refs=summary.evidence_refs,
        )

    def _normalize_value(self, value: str) -> str:
        return value.strip().casefold()

    def _remember_claimed_value(
        self,
        profile: ApplicantProfile,
        field_path: str,
        value: str,
    ) -> None:
        claim_history = profile.ds160_view.setdefault("field_claim_history", {})
        field_history = claim_history.setdefault(field_path, [])
        normalized_value = value.strip()
        if not normalized_value:
            return
        turn_match = self._find_claim_turn(profile, field_path, normalized_value)
        if field_history and field_history[-1].get("value") == normalized_value:
            if turn_match is not None and field_history[-1].get("turn_id") is None:
                field_history[-1].update(
                    {
                        "content": str(turn_match.get("content", normalized_value)),
                        "turn_id": turn_match.get("turn_id"),
                        "turn_index": turn_match.get("turn_index"),
                        "source": turn_match.get("source", "user_claim"),
                    }
                )
            return
        field_history.append(
            {
                "value": normalized_value,
                "content": (
                    str(turn_match.get("content", normalized_value))
                    if turn_match is not None
                    else normalized_value
                ),
                "turn_id": turn_match.get("turn_id") if turn_match is not None else None,
                "turn_index": (
                    turn_match.get("turn_index") if turn_match is not None else None
                ),
                "source": (
                    turn_match.get("source", "user_claim")
                    if turn_match is not None
                    else "user_claim"
                ),
            }
        )

    def _find_claim_turn(
        self,
        profile: ApplicantProfile,
        field_path: str,
        value: str,
    ) -> dict | None:
        turn_history = profile.ds160_view.get("turn_history", [])
        if not isinstance(turn_history, list):
            return None

        latest_user_turn: dict | None = None
        for turn in reversed(turn_history):
            if not isinstance(turn, dict) or turn.get("role") != "user":
                continue
            if latest_user_turn is None:
                latest_user_turn = turn
            if self._turn_matches_claim(field_path, value, str(turn.get("content", ""))):
                return turn
        return latest_user_turn

    def _turn_matches_claim(
        self,
        field_path: str,
        value: str,
        content: str,
    ) -> bool:
        normalized = content.casefold()
        if field_path == "/funding/primary_source":
            markers_by_value = {
                "parents": ("parent", "parents", "mother", "father", "mom", "dad"),
                "self": (
                    "myself",
                    "self",
                    "self-funded",
                    "self funded",
                    "i will pay",
                    "i will cover",
                    "savings",
                ),
                "relative": ("uncle", "aunt", "relative", "cousin", "brother", "sister"),
                "scholarship": ("scholarship", "assistantship", "stipend", "grant"),
            }
            return any(marker in normalized for marker in markers_by_value.get(value, ()))
        return value.casefold() in normalized

    def _record_document_snapshot(
        self,
        profile: ApplicantProfile,
        field_path: str,
        *,
        value: str | None,
        state: FieldState,
        evidence_refs: list[str],
    ) -> None:
        snapshots = profile.ds160_view.setdefault("document_evidence_snapshot", {})
        snapshots[field_path] = {
            "value": value,
            "state": state.value,
            "evidence_refs": list(evidence_refs),
        }

    def _clear_document_snapshot(
        self,
        profile: ApplicantProfile,
        field_path: str,
    ) -> None:
        snapshots = profile.ds160_view.setdefault("document_evidence_snapshot", {})
        snapshots.pop(field_path, None)
