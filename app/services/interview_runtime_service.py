from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.agents.schemas import AgentRuntimeDeps, InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import ApplicantProfile, GovernorDecision, ScoreState
from app.domain.runtime import (
    GovernorHistoryEntry,
    RiskFlagHistoryEntry,
    RuntimeTraceEntry,
    ScoreHistoryEntry,
)
from app.services.consistency_service import ConsistencyService
from app.services.evidence_service import EvidenceService
from app.services.extractor_service import ExtractorService
from app.services.retrieval_service import RetrievalService
from app.services.scoring_service import ScoringService


@dataclass
class InterviewTurnAnalysis:
    profile: ApplicantProfile
    trace_entries: list[RuntimeTraceEntry]
    score: ScoreState
    findings: list[dict[str, Any]]


class InterviewRuntimeService:
    def __init__(self, db: Session | Any) -> None:
        self.db = db
        self.model_factory = AgentModelFactory()
        self.extractor = ExtractorService(db)
        self.consistency = ConsistencyService()
        self.scoring = ScoringService(db)

    def analyze_turn(
        self,
        record: SessionRecord,
        message_text: str,
        recent_turns: list[Any] | None = None,
    ) -> InterviewTurnAnalysis:
        profile = self._load_profile(record.session_id, record.profile_json)
        trace_entries: list[RuntimeTraceEntry] = []

        trace_entries.append(self._receive_input())
        profile = self._extract_claims(
            record,
            profile,
            message_text,
            trace_entries,
            recent_turns=recent_turns,
        )
        self._resolve_evidence(profile, trace_entries)
        findings = self._consistency_check(profile, trace_entries)
        score = self._score_case(profile, findings, trace_entries)

        return InterviewTurnAnalysis(
            profile=profile,
            trace_entries=trace_entries,
            score=score,
            findings=findings,
        )

    def _receive_input(self) -> RuntimeTraceEntry:
        return RuntimeTraceEntry(
            node_name="receive_input",
            summary="user_message_received",
        )

    def _extract_claims(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        message_text: str,
        trace_entries: list[RuntimeTraceEntry],
        *,
        recent_turns: list[Any] | None = None,
    ) -> ApplicantProfile:
        profile.profile_version += 1
        profile.visa_intent["declared_family"] = record.declared_family
        previous_profile = profile.model_copy(deep=True)
        updated_profile = self.extractor.apply_message(
            profile,
            message_text,
            recent_turns=recent_turns,
        )
        updated_profile = self._preserve_gate_ready_fields(previous_profile, updated_profile)
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="extract_claims",
                summary=f"profile_version={updated_profile.profile_version}",
            )
        )
        return updated_profile

    def _resolve_evidence(
        self,
        profile: ApplicantProfile,
        trace_entries: list[RuntimeTraceEntry],
    ) -> None:
        documented_refs = {
            evidence_ref
            for provenance in profile.field_provenance.values()
            for evidence_ref in provenance.evidence_refs
        }
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="resolve_evidence",
                summary=f"documented_refs={len(documented_refs)}",
            )
        )

    def _consistency_check(
        self,
        profile: ApplicantProfile,
        trace_entries: list[RuntimeTraceEntry],
    ) -> list[dict[str, Any]]:
        findings = self.consistency.evaluate(profile)
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="consistency_check",
                summary=f"findings={len(findings)}",
            )
        )
        return findings

    def _score_case(
        self,
        profile: ApplicantProfile,
        findings: list[dict[str, Any]],
        trace_entries: list[RuntimeTraceEntry],
    ) -> ScoreState:
        score = self.scoring.propose(profile, findings, scoring_stage="interview_turn")
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="score_case",
                summary=self._score_summary(score),
            )
        )
        return score

    def build_question_action(
        self,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        trace_entries: list[RuntimeTraceEntry] | None = None,
        recent_turns: list[Any] | None = None,
    ) -> InterviewNextAction:
        action = self._question_action(
            session_id,
            profile,
            score,
            governor_decision,
            recent_turns=recent_turns,
        )
        if trace_entries is not None:
            trace_entries.append(
                RuntimeTraceEntry(
                    node_name="build_next_action",
                    summary=f"requested_documents={len(action.requested_documents)}",
                )
            )
        return action

    def _build_score_history_entry(self, score: ScoreState) -> ScoreHistoryEntry:
        return ScoreHistoryEntry(
            scoring_stage=score.scoring_stage,
            category_fit=score.category_fit,
            document_readiness=score.document_readiness,
            narrative_consistency=score.narrative_consistency,
            confidence=score.confidence,
            missing_evidence=list(score.missing_evidence),
            risk_flags=[
                RiskFlagHistoryEntry(
                    code=item.code,
                    severity=item.severity,
                    status=item.status,
                    evidence_refs=list(item.evidence_refs),
                )
                for item in score.risk_flags
            ],
            summary=self._score_summary(score),
        )

    def _build_governor_history_entry(self, decision: str) -> GovernorHistoryEntry:
        return GovernorHistoryEntry(
            decision=decision,
            summary=f"decision={decision}",
        )

    def _score_summary(self, score: ScoreState) -> str:
        return f"missing={len(score.missing_evidence)} risk_flags={len(score.risk_flags)}"

    def _load_profile(self, session_id: str, profile_json: dict) -> ApplicantProfile:
        if profile_json:
            return ApplicantProfile.model_validate(profile_json)
        return ApplicantProfile.minimal(profile_id=f"profile-{session_id}")

    def _preserve_gate_ready_fields(
        self,
        previous_profile: ApplicantProfile,
        updated_profile: ApplicantProfile,
    ) -> ApplicantProfile:
        field_path = "/funding/primary_source"
        previous_state = previous_profile.field_states.get(field_path)
        updated_state = updated_profile.field_states.get(field_path)
        if previous_state is None or updated_state is None:
            return updated_profile

        if previous_state.state not in {"documented", "confirmed"}:
            return updated_profile
        if updated_state.state not in {"claimed", "unknown"}:
            return updated_profile

        previous_provenance = previous_profile.field_provenance.get(field_path)
        if previous_provenance is None or not previous_provenance.evidence_refs:
            return updated_profile

        updated_profile.field_states[field_path] = previous_state.model_copy(deep=True)
        updated_profile.field_provenance[field_path] = previous_provenance.model_copy(
            deep=True
        )
        if "primary_source" in previous_profile.funding:
            updated_profile.funding["primary_source"] = previous_profile.funding["primary_source"]
        return updated_profile

    def _question_action(
        self,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        recent_turns: list[Any] | None = None,
    ) -> InterviewNextAction:
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return self._fallback_question_action(governor_decision, score)

        declared_family = profile.visa_intent.get("declared_family")
        model, runtime = self._build_question_agent_runtime(declared_family)
        if model is not None:
            try:
                action = QuestionAgentRunner(
                    model=model,
                    instructions=runtime.get("instructions")
                    or self.model_factory.build_instructions(
                        "question_agent",
                        declared_family=declared_family,
                    ),
                ).run(
                    deps=self._build_agent_deps(session_id),
                    profile_payload=profile.model_dump(mode="json"),
                    score_payload=score.model_dump(mode="json"),
                    governor_decision=governor_decision,
                )
                return self._finalize_question_action(governor_decision, score, action)
            except Exception:
                return self._fallback_question_action(
                    governor_decision,
                    score,
                    recent_turns=recent_turns,
                )
        return self._fallback_question_action(
            governor_decision,
            score,
            recent_turns=recent_turns,
        )

    def _build_question_agent_runtime(
        self,
        declared_family: str | None,
    ) -> tuple[Any | None, dict[str, Any]]:
        try:
            return self.model_factory.build(
                "question_agent",
                "interview_turn",
                declared_family=declared_family,
            )
        except TypeError as exc:
            if "declared_family" not in str(exc):
                raise
            return self.model_factory.build("question_agent", "interview_turn")

    def _build_agent_deps(self, session_id: str) -> AgentRuntimeDeps:
        return AgentRuntimeDeps(
            session_id=session_id,
            retrieval=RetrievalService(self.db),
            evidence=EvidenceService(self.db),
        )

    def _finalize_question_action(
        self,
        governor_decision: str,
        score: ScoreState,
        action: InterviewNextAction,
    ) -> InterviewNextAction:
        del score
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewNextAction(
                assistant_message=action.assistant_message,
                requested_documents=[],
                decision_hint=action.decision_hint,
            )

        requested_documents = self._coerce_requested_documents(action.requested_documents)
        return InterviewNextAction(
            assistant_message=action.assistant_message,
            requested_documents=requested_documents,
            decision_hint=action.decision_hint,
        )

    def _fallback_question_action(
        self,
        governor_decision: str,
        score: ScoreState,
        *,
        recent_turns: list[Any] | None = None,
    ) -> InterviewNextAction:
        del score
        if governor_decision == GovernorDecision.CONTINUE_INTERVIEW.value:
            return InterviewNextAction(
                assistant_message=self._next_continue_interview_question(recent_turns),
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
                requested_documents=[],
                decision_hint="high_risk_review",
            )
        return InterviewNextAction(
            assistant_message="Please provide the key supporting document for this point.",
            requested_documents=[],
            decision_hint="need_more_evidence",
        )

    def _coerce_requested_documents(
        self,
        *document_groups: list[str] | None,
    ) -> list[str]:
        for document_group in document_groups:
            if not document_group:
                continue
            for item in document_group:
                document_type = item.strip()
                if document_type:
                    return [document_type]
        return []

    def _next_continue_interview_question(
        self,
        recent_turns: list[Any] | None,
    ) -> str:
        previous_assistant_turn = None
        if recent_turns is not None:
            previous_assistant_turn = next(
                (turn for turn in reversed(recent_turns) if getattr(turn, "role", None) == "assistant"),
                None,
            )
        if previous_assistant_turn is None:
            return "What is the purpose of your travel?"

        lowered = previous_assistant_turn.content.lower()
        if "purpose of your travel" in lowered:
            return "Which school admitted you, and why did you choose it?"
        if "which school admitted you" in lowered:
            return "How will you pay for your studies?"
        return "What is the purpose of your travel?"
