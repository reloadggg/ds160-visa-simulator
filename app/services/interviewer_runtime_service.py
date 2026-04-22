from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    GovernorDecision,
    RiskFlag,
    ScoreState,
)
from app.domain.runtime import RuntimeTraceEntry
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.boundary_policy_service import BoundaryPolicyService
from app.services.governor_service import (
    DIRECT_REFUSAL_REASON_CODES,
    GovernorService,
)
from app.services.interview_runtime_service import InterviewRuntimeService
from app.services.interviewer_turn_projector_service import (
    InterviewerTurnProjection,
    InterviewerTurnProjectorService,
)
from app.services.risk_watch_service import RiskWatchService


class InterviewerRuntimeService:
    def __init__(self, db: Session | Any) -> None:
        self.db = db
        self.interview_runtime = InterviewRuntimeService(db)
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.governor = GovernorService()
        self.boundary_policy = BoundaryPolicyService(self.governor)
        self.turn_projector = InterviewerTurnProjectorService()
        self.risk_watch_service = RiskWatchService()

    def run_turn(self, record: SessionRecord, message_text: str) -> dict:
        history_turns = self.session_turn_repo.list_session_turns(record.session_id)
        history_turn_count = max(len(history_turns) - 1, 0)
        trace_entries, profile, score, findings = self._analyze_turn(
            record,
            message_text,
            history_turns,
        )
        profile_json = profile.model_dump(mode="json")
        governor, action = self._decide_and_build_action(
            record,
            profile,
            score,
            findings,
            history_turns,
            trace_entries,
        )
        action = self._coerce_action(action)
        projection = self.turn_projector.project(
            record=record,
            message_text=message_text,
            action=action,
            score=score,
            governor_decision=governor["decision"],
            governor_requested_documents=governor.get("requested_documents", []),
            trace_entries=trace_entries,
            history_turn_count=history_turn_count,
            history_turns=history_turns,
        )
        final_decision = action.decision

        self._apply_turn_state(
            record,
            profile_json=profile_json,
            final_decision=final_decision,
            projection=projection,
        )
        self.session_repo.append_runtime_history(
            record,
            runtime_trace=trace_entries,
            score_history=[self.interview_runtime._build_score_history_entry(score)],
            governor_history=[
                self.interview_runtime._build_governor_history_entry(governor["decision"])
            ],
        )

        return {
            "assistant_message": projection.response["assistant_message"],
            "governor_decision": projection.response["governor_decision"],
            "score_summary": dict(projection.response["score_summary"]),
            "requested_documents": list(projection.response["requested_documents"]),
            "turn_decision": dict(projection.response["turn_decision"]),
            "advisory_context": dict(projection.response["advisory_context"]),
            "prompt_trace": dict(projection.response["prompt_trace"]),
            "turn_record": projection.turn_record,
        }

    def _analyze_turn(
        self,
        record: SessionRecord,
        message_text: str,
        history_turns: list[Any],
    ) -> tuple[list[RuntimeTraceEntry], ApplicantProfile, ScoreState, list[Any]]:
        analysis = self.interview_runtime.analyze_turn(
            record,
            message_text,
            history_turns,
        )
        trace_entries = self._extract_runtime_trace(analysis)
        profile = self._extract_profile_model(analysis)
        score = self._extract_score_model(analysis)
        findings = self._extract_findings(analysis)
        if profile is None or score is None:
            raise ValueError("interview analysis must include profile and score")

        self._apply_risk_watch_signals(
            record,
            profile,
            score,
            history_turns,
            message_text,
        )
        return trace_entries, profile, score, findings

    def _decide_and_build_action(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        findings: list[Any],
        history_turns: list[Any],
        trace_entries: list[RuntimeTraceEntry],
    ) -> tuple[dict[str, Any], Any]:
        governor = self._decide_governor(
            record,
            profile,
            score,
            trace_entries,
            findings=findings,
        )
        action = self._build_next_action(
            record,
            profile,
            score,
            governor["decision"],
            history_turns,
            trace_entries,
        )
        return governor, action

    def _apply_turn_state(
        self,
        record: SessionRecord,
        *,
        profile_json: dict[str, Any],
        final_decision: str,
        projection: InterviewerTurnProjection,
    ) -> None:
        record.profile_json = profile_json
        record.current_governor_decision = final_decision
        record.current_focus_json = projection.current_focus
        record.phase_state = projection.phase_state
        record.interviewer_state_json = projection.interviewer_state

    def _extract_profile_model(self, analysis: Any) -> ApplicantProfile | None:
        profile = self._analysis_value(analysis, "profile")
        if profile is not None:
            return profile
        return None

    def _extract_score_model(self, analysis: Any) -> ScoreState | None:
        score = self._analysis_value(analysis, "score")
        if score is None:
            return None
        if isinstance(score, ScoreState):
            return score
        return ScoreState.model_validate(score)

    def _extract_findings(self, analysis: Any) -> list[Any]:
        findings = self._analysis_value(analysis, "findings", [])
        if not isinstance(findings, list):
            return []
        return list(findings)

    def _decide_governor(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        trace_entries: list[RuntimeTraceEntry],
        findings: list[Any] | None = None,
    ) -> dict[str, Any]:
        review_signal = self._high_risk_review_signal(profile, score)
        governor = self.boundary_policy.decide(
            profile,
            score,
            self._build_early_term_candidate(
                record.declared_family,
                findings or [],
            ),
            review_signal=review_signal,
        )
        trace_entries.append(
            RuntimeTraceEntry(
                node_name="governor_decide",
                summary=f"decision={governor['decision']}",
            )
        )
        return governor

    def _build_next_action(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        governor_decision: str,
        history_turns: list,
        trace_entries: list[RuntimeTraceEntry],
        ) -> InterviewNextAction:
        return self.interview_runtime.build_question_action(
            record.session_id,
            profile,
            score,
            governor_decision,
            trace_entries,
            history_turns,
        )

    def _coerce_action(self, action: Any) -> InterviewNextAction:
        if isinstance(action, InterviewNextAction):
            return action
        if isinstance(action, dict):
            return InterviewNextAction.model_validate(action)
        return InterviewNextAction.model_validate(
            {
                "assistant_message": getattr(action, "assistant_message"),
                "requested_documents": list(
                    getattr(action, "requested_documents", []) or []
                ),
                "decision": getattr(action, "decision", None)
                or getattr(action, "decision_hint"),
                "focus_kind": getattr(action, "focus_kind", None),
                "focus_document_type": getattr(action, "focus_document_type", None),
                "focus_risk_code": getattr(action, "focus_risk_code", None),
                "reason": getattr(action, "reason", None),
            }
        )

    def _extract_runtime_trace(self, analysis: Any) -> list[Any]:
        return list(self._analysis_value(analysis, "runtime_trace", [])) or list(
            self._analysis_value(analysis, "trace_entries", [])
        )

    def _build_early_term_candidate(
        self,
        declared_family: str | None,
        findings: list[Any],
    ) -> dict | None:
        family = declared_family or "unknown"
        confirmed_terminal_findings = [
            finding
            for finding in findings
            if (
                self._finding_value(finding, "severity") == "high"
                and self._finding_value(finding, "status") == "confirmed"
                and self._finding_value(finding, "evidence_refs")
            )
        ]
        if not confirmed_terminal_findings:
            return None

        prioritized_finding = next(
            (
                finding
                for finding in confirmed_terminal_findings
                if self._finding_value(finding, "finding_type")
                in DIRECT_REFUSAL_REASON_CODES
            ),
            confirmed_terminal_findings[0],
        )
        reason_code = self._finding_value(prioritized_finding, "finding_type")
        evidence_refs = list(
            self._finding_value(prioritized_finding, "evidence_refs", [])
        )
        return {
            "eligible": True,
            "policy_id": f"{family}.tp.{reason_code}",
            "reason_code": reason_code,
            "confirmation_required": False,
            "evidence_refs": evidence_refs,
        }

    def _finding_value(
        self,
        finding: Any,
        key: str,
        default: Any = None,
    ) -> Any:
        if isinstance(finding, dict):
            return finding.get(key, default)
        return getattr(finding, key, default)

    def _high_risk_review_signal(
        self,
        profile: ApplicantProfile,
        score: ScoreState,
    ) -> RiskFlag | None:
        return self.risk_watch_service.high_risk_review_signal(profile, score)

    def _analysis_value(self, analysis: Any, key: str, default: Any = None) -> Any:
        if isinstance(analysis, dict):
            return analysis.get(key, default)
        return getattr(analysis, key, default)

    def _apply_risk_watch_signals(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        history_turns: list[Any],
        message_text: str,
    ) -> None:
        self.risk_watch_service.apply_risk_watch_signals(
            record,
            profile,
            score,
            history_turns,
            message_text,
        )

    def _is_evasive_answer(
        self,
        focus_question: str,
        message_text: str,
    ) -> bool:
        return self.risk_watch_service.is_evasive_answer(
            focus_question,
            message_text,
        )
