from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    GovernorDecision,
    ScoreState,
)
from app.domain.document_types import normalize_document_type
from app.domain.runtime import GateOverallStatus, RuntimeTraceEntry
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.interview_runtime_service import InterviewRuntimeService
from app.services.interviewer_turn_projector_service import (
    InterviewerTurnProjection,
    InterviewerTurnProjectorService,
)


class InterviewerRuntimeService:
    def __init__(self, db: Session | Any) -> None:
        self.db = db
        self.interview_runtime = InterviewRuntimeService(db)
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.turn_projector = InterviewerTurnProjectorService()

    def run_turn(self, record: SessionRecord, message_text: str) -> dict:
        history_turns = self.session_turn_repo.list_session_turns(record.session_id)
        history_turn_count = max(len(history_turns) - 1, 0)
        trace_entries, profile, score, findings = self._analyze_turn(
            record,
            message_text,
            history_turns,
        )
        score = self._reconcile_score_with_gate(record, score)
        profile_json = profile.model_dump(mode="json")
        governor, action, capability_tool_outputs = self._decide_and_build_action(
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
            capability_tool_outputs=capability_tool_outputs,
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
        return trace_entries, profile, score, findings

    def _decide_and_build_action(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        findings: list[Any],
        history_turns: list[Any],
        trace_entries: list[RuntimeTraceEntry],
    ) -> tuple[dict[str, Any], Any, dict[str, Any]]:
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
        action = self._coerce_action(action)
        final_governor = {
            **governor,
            "decision": action.decision,
            "requested_documents": list(
                action.requested_documents or governor.get("requested_documents", [])
            ),
        }
        return final_governor, action, dict(self.interview_runtime._last_capability_tool_outputs)


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


    def _reconcile_score_with_gate(
        self,
        record: SessionRecord,
        score: ScoreState,
    ) -> ScoreState:
        ready_documents = self._ready_gate_documents(record)
        if not ready_documents:
            return score
        next_score = score.model_copy(deep=True)
        original_missing = list(next_score.missing_evidence)
        next_score.missing_evidence = [
            item
            for item in next_score.missing_evidence
            if normalize_document_type(item) not in ready_documents
        ]
        removed_missing = len(next_score.missing_evidence) != len(original_missing)
        if removed_missing and not next_score.missing_evidence:
            next_score.risk_flags = [
                flag
                for flag in next_score.risk_flags
                if flag.code != "supporting_evidence_missing"
            ]
        return next_score

    def _ready_gate_documents(self, record: SessionRecord) -> set[str]:
        ready_documents: set[str] = set()
        for item in (record.gate_status_json or {}).get("required_documents", []):
            if not isinstance(item, dict) or item.get("status") != "ready":
                continue
            document_type = normalize_document_type(item.get("document_type"))
            if document_type is not None:
                ready_documents.add(document_type)
        return ready_documents

    def _decide_governor(
        self,
        record: SessionRecord,
        profile: ApplicantProfile,
        score: ScoreState,
        trace_entries: list[RuntimeTraceEntry],
        findings: list[Any] | None = None,
    ) -> dict[str, Any]:
        del profile, trace_entries, findings
        current_decision = (
            record.current_governor_decision
            or GovernorDecision.CONTINUE_INTERVIEW.value
        )
        if (
            current_decision == GovernorDecision.NEED_MORE_EVIDENCE.value
            and not score.missing_evidence
        ):
            current_decision = GovernorDecision.CONTINUE_INTERVIEW.value
        return {
            "decision": current_decision,
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        }

    def _gate_is_ready(self, record: SessionRecord) -> bool:
        return (record.gate_status_json or {}).get("status") == GateOverallStatus.READY_FOR_INTERVIEW

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

    def _analysis_value(self, analysis: Any, key: str, default: Any = None) -> Any:
        if isinstance(analysis, dict):
            return analysis.get(key, default)
        return getattr(analysis, key, default)

    def _is_evasive_answer(
        self,
        focus_question: str,
        message_text: str,
    ) -> bool:
        from app.services.risk_watch_service import RiskWatchService

        return RiskWatchService().is_evasive_answer(focus_question, message_text)
