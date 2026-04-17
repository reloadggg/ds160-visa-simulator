from sqlalchemy.orm import Session

from app.domain.contracts import ApplicantProfile
from app.repositories.session_repo import SessionRepository
from app.services.consistency_service import ConsistencyService
from app.services.extractor_service import ExtractorService
from app.services.governor_service import GovernorService
from app.services.scoring_service import ScoringService


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.extractor = ExtractorService()
        self.consistency = ConsistencyService()
        self.scoring = ScoringService()
        self.governor = GovernorService()

    def handle_user_turn(self, session_id: str, message_text: str) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)

        profile = self._load_profile(record.session_id, record.profile_json)
        profile.profile_version += 1
        profile.visa_intent["declared_family"] = record.declared_family
        profile = self.extractor.apply_message(profile, message_text)
        findings = self.consistency.evaluate(profile)
        score = self.scoring.propose(profile, findings, scoring_stage="interview_turn")
        governor = self.governor.decide(profile, score, early_term_candidate=None)

        record.profile_json = profile.model_dump(mode="json")
        record.current_governor_decision = governor["decision"]
        self.session_repo.save(record)

        assistant_message = "Please upload funding proof."
        if not score.missing_evidence:
            assistant_message = "What is the purpose of your travel?"

        return {
            "assistant_message": assistant_message,
            "governor_decision": governor["decision"],
            "score_summary": {
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            "requested_documents": governor["requested_documents"],
        }

    def _load_profile(self, session_id: str, profile_json: dict) -> ApplicantProfile:
        if profile_json:
            return ApplicantProfile.model_validate(profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{session_id}")
