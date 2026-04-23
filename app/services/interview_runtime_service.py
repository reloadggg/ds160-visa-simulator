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
    PromptRoleContract,
    RiskFlagHistoryEntry,
    RuntimeTraceEntry,
    ScoreHistoryEntry,
    TurnAdvisoryContext,
)
from app.repositories.session_repo import SessionRepository
from app.repositories.document_repo import DocumentRepository
from app.services.advisory_review_service import AdvisoryReviewService
from app.services.capability_orchestrator import CapabilityOrchestrator
from app.services.consistency_service import ConsistencyService
from app.services.ds160_context_engine import DS160ContextEngine
from app.services.ds160_memory_manager import DS160MemoryManager
from app.services.evidence_service import EvidenceService
from app.services.extractor_service import ExtractorService
from app.services.retrieval_service import RetrievalService
from app.services.scoring_service import ScoringService
from app.services.session_read_model_service import SessionReadModelService


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
        self.session_repo = SessionRepository(db)
        self.document_repo = DocumentRepository(db)
        self.session_read_model = SessionReadModelService(db)
        self.extractor = ExtractorService(db)
        self.consistency = ConsistencyService()
        self.scoring = ScoringService(db)
        self.advisory_review = AdvisoryReviewService()
        self.memory_manager = DS160MemoryManager()
        self.context_engine = DS160ContextEngine()
        self.capability_orchestrator = CapabilityOrchestrator(db)
        self._last_capability_trace_entries: list[RuntimeTraceEntry] = []

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
        self._last_capability_trace_entries = []
        action, runtime_trace = self._question_action(
            session_id,
            profile,
            score,
            governor_decision,
            recent_turns=recent_turns,
        )
        if trace_entries is not None:
            trace_entries.extend(self._last_capability_trace_entries)
            trace_entries.append(runtime_trace)
        self._last_capability_trace_entries = []
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
    ) -> tuple[InterviewNextAction, RuntimeTraceEntry]:
        self._last_capability_trace_entries = []
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            action = self._fallback_question_action(governor_decision, score)
            return action, self._build_turn_decision_trace(
                runtime={},
                action=action,
                fallback_used=True,
                tool_calls=[],
                retry_count=0,
                provider=None,
                model=None,
                boundary_decision=governor_decision,
            )

        declared_family = profile.visa_intent.get("declared_family")
        model, runtime = self._build_question_agent_runtime(declared_family)
        fallback_messages = runtime.get("fallback_messages", {})
        latest_user_message = self._latest_user_message(recent_turns)
        dynamic_turn_context = self._build_dynamic_turn_context(
            session_id=session_id,
            profile=profile,
            score=score,
            governor_decision=governor_decision,
            recent_turns=recent_turns,
            latest_user_message=latest_user_message,
            declared_family=declared_family,
        )
        capability_result = self.capability_orchestrator.orchestrate(
            session_id=session_id,
            governor_decision=governor_decision,
            latest_user_message=latest_user_message,
            dynamic_turn_context=dynamic_turn_context,
            score=score,
        )
        dynamic_turn_context["capability_plan"] = list(
            capability_result.capability_plan
        )
        self._last_capability_trace_entries = list(capability_result.trace_entries)
        if model is not None:
            try:
                run_result = QuestionAgentRunner(
                    model=model,
                    instructions=runtime.get("instructions")
                    or self.model_factory.build_instructions(
                        "question_agent",
                        declared_family=declared_family,
                    ),
                ).run(
                    deps=self._build_agent_deps(session_id),
                    dynamic_turn_context=dynamic_turn_context,
                    tool_outputs=capability_result.tool_outputs,
                    user_message=latest_user_message,
                    boundary_decision=governor_decision,
                )
                action = self._finalize_question_action(
                    governor_decision,
                    score,
                    run_result.output,
                )
                return action, self._build_turn_decision_trace(
                    runtime=runtime,
                    action=action,
                    fallback_used=False,
                    tool_calls=run_result.tool_calls,
                    retry_count=run_result.retry_count,
                    provider=run_result.provider or runtime.get("provider"),
                    model=run_result.model or runtime.get("model"),
                    boundary_decision=governor_decision,
                )
            except Exception:
                action = self._fallback_question_action(
                    governor_decision,
                    score,
                    recent_turns=recent_turns,
                    fallback_messages=fallback_messages,
                )
                return action, self._build_turn_decision_trace(
                    runtime=runtime,
                    action=action,
                    fallback_used=True,
                    tool_calls=[],
                    retry_count=0,
                    provider=runtime.get("provider"),
                    model=runtime.get("model"),
                    boundary_decision=governor_decision,
                )
        action = self._fallback_question_action(
            governor_decision,
            score,
            recent_turns=recent_turns,
            fallback_messages=fallback_messages,
        )
        return action, self._build_turn_decision_trace(
            runtime=runtime,
            action=action,
            fallback_used=True,
            tool_calls=[],
            retry_count=0,
            provider=runtime.get("provider"),
            model=runtime.get("model"),
            boundary_decision=governor_decision,
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
                decision="high_risk_review",
                assistant_message=action.assistant_message,
                requested_documents=[],
                focus_kind="risk_review",
                focus_risk_code=action.focus_risk_code,
                reason=action.reason,
            )

        requested_documents = self._coerce_requested_documents(action.requested_documents)
        return InterviewNextAction(
            decision=action.decision,
            assistant_message=action.assistant_message,
            requested_documents=requested_documents,
            focus_kind=action.focus_kind,
            focus_document_type=action.focus_document_type,
            focus_risk_code=action.focus_risk_code,
            reason=action.reason,
        )

    def _fallback_question_action(
        self,
        governor_decision: str,
        score: ScoreState,
        *,
        recent_turns: list[Any] | None = None,
        fallback_messages: dict[str, str] | None = None,
    ) -> InterviewNextAction:
        fallback_messages = fallback_messages or {}
        fallback_requested_documents = self._coerce_requested_documents(
            score.missing_evidence
        )
        if (
            governor_decision == GovernorDecision.CONTINUE_INTERVIEW.value
            and fallback_requested_documents
        ):
            return InterviewNextAction(
                decision="need_more_evidence",
                assistant_message=fallback_messages.get("need_more_evidence")
                or "Please provide the key supporting document for this point.",
                requested_documents=fallback_requested_documents,
                focus_kind="required_document",
                focus_document_type=fallback_requested_documents[0],
            )
        if governor_decision == GovernorDecision.CONTINUE_INTERVIEW.value:
            return InterviewNextAction(
                decision="continue_interview",
                assistant_message=fallback_messages.get("continue_interview")
                or self._next_continue_interview_question(recent_turns),
                requested_documents=[],
                focus_kind="interview_question",
            )
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewNextAction(
                decision="simulated_refusal",
                assistant_message=fallback_messages.get("simulated_refusal")
                or "This simulated case results in refusal based on confirmed record conflicts.",
                requested_documents=[],
                focus_kind="refusal",
            )
        if governor_decision == GovernorDecision.ROUTE_CORRECTION.value:
            return InterviewNextAction(
                decision="route_correction",
                assistant_message=fallback_messages.get("route_correction")
                or "Your case may fit a different visa route. Please clarify your travel purpose.",
                requested_documents=[],
                focus_kind="route_correction",
            )
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewNextAction(
                decision="high_risk_review",
                assistant_message=fallback_messages.get("high_risk_review")
                or "This case needs additional review before the interview can continue.",
                requested_documents=[],
                focus_kind="risk_review",
            )
        return InterviewNextAction(
            decision="need_more_evidence",
            assistant_message=fallback_messages.get("need_more_evidence")
            or "Please provide the key supporting document for this point.",
            requested_documents=fallback_requested_documents,
            focus_kind="required_document",
            focus_document_type=(
                fallback_requested_documents[0]
                if fallback_requested_documents
                else "supporting_document"
            ),
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

    def _latest_user_message(self, recent_turns: list[Any] | None) -> str:
        if recent_turns is None:
            return ""
        for turn in reversed(recent_turns):
            if getattr(turn, "role", None) != "user":
                continue
            content = getattr(turn, "content", "")
            if isinstance(content, str):
                return content
        return ""

    def _build_dynamic_turn_context(
        self,
        *,
        session_id: str,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        recent_turns: list[Any] | None,
        latest_user_message: str,
        declared_family: str | None,
    ) -> dict[str, Any]:
        record = self.session_repo.get(session_id)
        phase_state = getattr(record, "phase_state", None) or "interview"
        gate_progress = (
            dict(getattr(record, "gate_status_json", {}) or {})
            if record is not None
            else {}
        )
        read_model = None
        documents = []
        if record is not None:
            read_model = self.session_read_model.build_from_record(
                record,
                turns=recent_turns,
            )
            documents = self.document_repo.list_session_documents(session_id)
        advisory_context = self._build_advisory_context(score)
        memory_bundle = self.memory_manager.build(
            profile=profile,
            score=score,
            advisory_context=advisory_context,
            read_model=read_model,
            declared_family=declared_family,
            phase_state=phase_state,
            boundary_decision=governor_decision,
            documents=documents,
        )
        snapshot = self.context_engine.build_dynamic_turn_context(
            session_id=session_id,
            declared_family=declared_family,
            phase_state=phase_state,
            latest_user_message=latest_user_message,
            profile=profile,
            advisory_context=advisory_context,
            gate_progress=gate_progress,
            recent_turns=recent_turns,
            memory_bundle=memory_bundle,
            capability_plan=[],
            prompt_roles=PromptRoleContract(),
        )
        return snapshot.model_dump(mode="json")

    def _build_advisory_context(self, score: ScoreState) -> TurnAdvisoryContext:
        return self.advisory_review.build_context(score)

    def _risk_level_from_score(self, score: ScoreState) -> InterviewRiskLevel:
        return self.advisory_review.derive_risk_level(score)

    def _build_turn_decision_trace(
        self,
        *,
        runtime: dict[str, Any],
        action: InterviewNextAction,
        fallback_used: bool,
        tool_calls: list[dict[str, Any]],
        retry_count: int,
        provider: str | None,
        model: str | None,
        boundary_decision: str,
    ) -> RuntimeTraceEntry:
        return RuntimeTraceEntry(
            node_name="turn_decision",
            summary=f"decision={action.decision}",
            prompt_pack_id=runtime.get("prompt_pack_id"),
            prompt_version=runtime.get("prompt_version"),
            provider=provider,
            model=model,
            tool_calls=tool_calls,
            turn_decision=action.decision,
            fallback_used=fallback_used,
            retry_count=retry_count,
            metadata={
                "requested_documents": list(action.requested_documents),
                "focus_kind": action.focus_kind,
                "focus_document_type": action.focus_document_type,
                "boundary_decision": boundary_decision,
                "reasoning_effort": runtime.get("reasoning_effort"),
            },
        )
