from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.domain.contracts import ApplicantProfile, GovernorDecision
from app.repositories.session_repo import SessionRepository
from app.services.evidence_service import EvidenceService
from app.services.consistency_service import ConsistencyService
from app.services.extractor_service import ExtractorService
from app.services.governor_service import GovernorService
from app.services.retrieval_service import RetrievalService
from app.services.scoring_service import ScoringService


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.model_factory = AgentModelFactory()
        self.extractor = ExtractorService(db)
        self.consistency = ConsistencyService()
        self.scoring = ScoringService(db)
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
        early_term_candidate = self._build_early_term_candidate(
            record.declared_family,
            score,
        )
        governor = self.governor.decide(profile, score, early_term_candidate)
        action = self._question_action(record.session_id, profile, score, governor["decision"])

        record.profile_json = profile.model_dump(mode="json")
        record.current_governor_decision = governor["decision"]
        self.session_repo.save(record)

        return {
            "assistant_message": action.assistant_message,
            "governor_decision": governor["decision"],
            "score_summary": {
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            "requested_documents": action.requested_documents,
        }

    def _load_profile(self, session_id: str, profile_json: dict) -> ApplicantProfile:
        if profile_json:
            return ApplicantProfile.model_validate(profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{session_id}")

    def _build_early_term_candidate(
        self,
        declared_family: str | None,
        score,
    ) -> dict | None:
        family = declared_family or "unknown"
        for risk_flag in score.risk_flags:
            if (
                risk_flag.severity == "high"
                and risk_flag.status == "confirmed"
                and risk_flag.evidence_refs
            ):
                return {
                    "eligible": True,
                    "policy_id": f"{family}.tp.{risk_flag.code}",
                    "confirmation_required": False,
                    "evidence_refs": risk_flag.evidence_refs,
                }
        return None

    def _question_action(
        self,
        session_id: str,
        profile: ApplicantProfile,
        score,
        governor_decision: str,
    ) -> InterviewNextAction:
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return self._fallback_question_action(governor_decision, score)

        model, _runtime = self.model_factory.build("question_agent", "interview_turn")
        if model is not None:
            try:
                action = QuestionAgentRunner(model=model).run(
                    deps=self._build_agent_deps(session_id),
                    profile_payload=profile.model_dump(mode="json"),
                    score_payload=score.model_dump(mode="json"),
                    governor_decision=governor_decision,
                )
            except Exception:
                return self._fallback_question_action(governor_decision, score)
            return self._finalize_question_action(governor_decision, score, action)
        return self._fallback_question_action(governor_decision, score)

    def _build_agent_deps(self, session_id: str) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=session_id,
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
        )

    def _finalize_question_action(
        self,
        governor_decision: str,
        score,
        action: InterviewNextAction,
    ) -> InterviewNextAction:
        requested_documents = list(action.requested_documents)
        if governor_decision == GovernorDecision.NEED_MORE_EVIDENCE.value and not requested_documents:
            requested_documents = list(score.missing_evidence)

        return InterviewNextAction(
            assistant_message=action.assistant_message,
            requested_documents=requested_documents,
            decision_hint=action.decision_hint,
        )

    def _fallback_question_action(
        self,
        governor_decision: str,
        score,
    ) -> InterviewNextAction:
        if governor_decision == GovernorDecision.CONTINUE_INTERVIEW.value:
            return InterviewNextAction(
                assistant_message="What is the purpose of your travel?",
                requested_documents=[],
                decision_hint="continue_interview",
            )
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewNextAction(
                assistant_message=(
                    "This simulated case results in refusal based on confirmed record conflicts."
                ),
                requested_documents=[],
                decision_hint="simulated_refusal",
            )
        if governor_decision == GovernorDecision.ROUTE_CORRECTION.value:
            return InterviewNextAction(
                assistant_message="Your case may fit a different visa route. Please clarify your travel purpose.",
                requested_documents=[],
                decision_hint="route_correction",
            )
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewNextAction(
                assistant_message="This case needs additional review before the interview can continue.",
                requested_documents=list(score.missing_evidence),
                decision_hint="high_risk_review",
            )
        return InterviewNextAction(
            assistant_message="Please upload funding proof.",
            requested_documents=list(score.missing_evidence),
            decision_hint="need_more_evidence",
        )
