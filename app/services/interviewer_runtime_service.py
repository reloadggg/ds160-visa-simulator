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
        boundary, action, capability_tool_outputs = self._decide_and_build_action(
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
            governor_decision=boundary["decision"],
            governor_requested_documents=boundary.get("requested_documents", []),
            capability_tool_outputs=capability_tool_outputs,
            trace_entries=trace_entries,
            history_turn_count=history_turn_count,
            history_turns=history_turns,
        )

        self._apply_turn_state(
            record,
            profile_json=profile_json,
            boundary_decision=boundary["decision"],
            projection=projection,
        )
        self.session_repo.append_runtime_history(
            record,
            runtime_trace=trace_entries,
            score_history=[self.interview_runtime._build_score_history_entry(score)],
            governor_history=[
                self.interview_runtime._build_governor_history_entry(boundary["decision"])
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

    def refresh_after_material_change(
        self,
        record: SessionRecord,
        *,
        reason: str,
    ) -> dict:
        history_turns = self.session_turn_repo.list_session_turns(record.session_id)
        history_turn_count = max(len(history_turns), 0)
        trace_entries, profile, score, findings = self._analyze_material_change(
            record,
            reason=reason,
        )
        score = self._reconcile_score_with_gate(record, score)
        profile_json = profile.model_dump(mode="json")
        boundary, action, capability_tool_outputs = self._decide_and_build_action(
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
            message_text=reason,
            action=action,
            score=score,
            governor_decision=boundary["decision"],
            governor_requested_documents=boundary.get("requested_documents", []),
            capability_tool_outputs=capability_tool_outputs,
            trace_entries=trace_entries,
            history_turn_count=history_turn_count,
            history_turns=history_turns,
        )

        self._apply_turn_state(
            record,
            profile_json=profile_json,
            boundary_decision=boundary["decision"],
            projection=projection,
        )
        self.session_repo.append_runtime_history(
            record,
            runtime_trace=trace_entries,
            score_history=[self.interview_runtime._build_score_history_entry(score)],
            governor_history=[
                self.interview_runtime._build_governor_history_entry(boundary["decision"])
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

    def _analyze_material_change(
        self,
        record: SessionRecord,
        *,
        reason: str,
    ) -> tuple[list[RuntimeTraceEntry], ApplicantProfile, ScoreState, list[Any]]:
        analysis = self.interview_runtime.analyze_material_change(
            record,
            reason=reason,
        )
        trace_entries = self._extract_runtime_trace(analysis)
        profile = self._extract_profile_model(analysis)
        score = self._extract_score_model(analysis)
        findings = self._extract_findings(analysis)
        if profile is None or score is None:
            raise ValueError("material change analysis must include profile and score")
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
        boundary = self._decide_governor(
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
            boundary["decision"],
            history_turns,
            trace_entries,
        )
        action = self._coerce_action(action)
        action = self._clear_ready_document_focus(record, action)
        pre_convergence_action = action
        action = self._converge_repeated_claim_conflict(
            action,
            score=score,
            capability_tool_outputs=dict(
                self.interview_runtime._last_capability_tool_outputs
            ),
            history_turns=history_turns,
        )
        if action != pre_convergence_action:
            self._sync_last_turn_decision_trace(trace_entries, action)
        boundary = self._apply_boundary_transition(boundary, action)
        action = self._align_action_with_document_review(
            action,
            capability_tool_outputs=dict(
                self.interview_runtime._last_capability_tool_outputs
            ),
        )
        boundary = self._apply_boundary_transition(boundary, action)
        return boundary, action, dict(self.interview_runtime._last_capability_tool_outputs)


    def _apply_turn_state(
        self,
        record: SessionRecord,
        *,
        profile_json: dict[str, Any],
        boundary_decision: str,
        projection: InterviewerTurnProjection,
    ) -> None:
        record.profile_json = profile_json
        record.current_governor_decision = boundary_decision
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
        current_decision = self._boundary_decision_from_record(record)
        return {
            "decision": current_decision,
            "blocked_actions": [],
            "rationale_refs": [],
            "requested_documents": [],
        }

    def _boundary_decision_from_record(self, record: SessionRecord) -> str:
        current_decision = record.current_governor_decision
        if current_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return GovernorDecision.SIMULATED_REFUSAL.value
        return GovernorDecision.CONTINUE_INTERVIEW.value

    def _apply_boundary_transition(
        self,
        boundary: dict[str, Any],
        action: InterviewNextAction,
    ) -> dict[str, Any]:
        if action.decision not in {
            GovernorDecision.HIGH_RISK_REVIEW.value,
            GovernorDecision.SIMULATED_REFUSAL.value,
        }:
            return boundary
        return {
            **boundary,
            "decision": action.decision,
        }

    def _align_action_with_document_review(
        self,
        action: InterviewNextAction,
        *,
        capability_tool_outputs: dict[str, Any],
    ) -> InterviewNextAction:
        document_review = capability_tool_outputs.get("document_review")
        if not isinstance(document_review, dict):
            return action
        if action.decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return action
        cross_document_conflicts = [
            item
            for item in document_review.get("cross_document_conflicts", []) or []
            if isinstance(item, dict)
        ]
        claim_conflicts = [
            item
            for item in document_review.get("claim_conflicts", []) or []
            if isinstance(item, dict)
        ]
        conflicts = [
            item
            for item in [*cross_document_conflicts, *claim_conflicts]
        ]
        high_conflict = next(
            (
                item
                for item in conflicts
                if item.get("severity") == "high"
            ),
            None,
        )
        high_cross_document_conflict = next(
            (
                item
                for item in cross_document_conflicts
                if item.get("severity") == "high"
            ),
            None,
        )
        review_status = document_review.get("review_status")
        if review_status != "high_risk" and high_cross_document_conflict is None:
            return action
        if review_status != "high_risk" and action.decision != "continue_interview":
            return action
        high_conflict = (
            high_conflict if review_status == "high_risk" else high_cross_document_conflict
        )
        if high_conflict is None:
            return action
        summary = self._runtime_text(high_conflict.get("summary")) or (
            self._runtime_text(document_review.get("reviewer_summary"))
            or "材料核验发现关键冲突。"
        )
        return InterviewNextAction(
            decision=GovernorDecision.HIGH_RISK_REVIEW.value,
            assistant_message=f"{summary} 我们先停在这个点，请你解释这处不一致。",
            requested_documents=[],
            focus_kind="risk_review",
            focus_document_type=None,
            focus_risk_code="record_conflict",
            reason="document_review_high_risk",
        )

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

    def _clear_ready_document_focus(
        self,
        record: SessionRecord,
        action: InterviewNextAction,
    ) -> InterviewNextAction:
        if action.decision != GovernorDecision.NEED_MORE_EVIDENCE.value:
            return action
        ready_documents = self._ready_gate_documents(record)
        if not ready_documents:
            return action
        if not action.requested_documents and not action.focus_document_type:
            return action
        requested_documents = [
            document_type
            for document_type in action.requested_documents
            if normalize_document_type(document_type) not in ready_documents
        ]
        focus_document_type = action.focus_document_type
        if normalize_document_type(focus_document_type) in ready_documents:
            focus_document_type = None
        if requested_documents or focus_document_type:
            return action.model_copy(
                update={
                    "requested_documents": requested_documents,
                    "focus_document_type": focus_document_type,
                }
            )
        return InterviewNextAction(
            decision=GovernorDecision.CONTINUE_INTERVIEW.value,
            assistant_message="材料核验已更新，我们继续面谈：请你说明这次赴美学习的主要目的。",
            requested_documents=[],
            focus_kind="interview_question",
            focus_document_type=None,
            focus_risk_code=None,
            reason="cleared_ready_document_request_after_material_change",
        )

    def _converge_repeated_claim_conflict(
        self,
        action: InterviewNextAction,
        *,
        score: ScoreState,
        capability_tool_outputs: dict[str, Any],
        history_turns: list[Any],
    ) -> InterviewNextAction:
        if (
            action.decision == GovernorDecision.SIMULATED_REFUSAL.value
            and self._has_refusal_redline(score)
        ):
            return action
        conflict = self._primary_high_claim_conflict(capability_tool_outputs)
        if conflict is None:
            return action
        if self._repeated_claim_conflict_count(
            history_turns,
            target_conflict=conflict,
        ) < 2:
            return action
        risk_code = self._first_score_risk_code(score) or "record_conflict"
        summary = self._runtime_text(conflict.get("summary")) or (
            "你的连续回答仍与已提交材料存在核心冲突。"
        )
        return InterviewNextAction(
            decision=GovernorDecision.HIGH_RISK_REVIEW.value,
            assistant_message=(
                f"{summary} 这个冲突已经无法通过继续重复追问澄清，"
                "当前案例需要先进入高风险复核。"
            ),
            requested_documents=[],
            focus_kind="risk_review",
            focus_document_type=None,
            focus_risk_code=risk_code,
            reason="repeated_claim_document_conflict",
        )

    def _has_refusal_redline(self, score: ScoreState) -> bool:
        return any(
            self._runtime_text(risk_flag.code) in {"hard_conflict", "fraud_admission"}
            for risk_flag in score.risk_flags
        )

    def _repeated_claim_conflict_count(
        self,
        history_turns: list[Any],
        *,
        target_conflict: dict[str, Any],
    ) -> int:
        target_fingerprint = self._claim_conflict_fingerprint(target_conflict)
        count = 0
        for turn in reversed(history_turns):
            if getattr(turn, "role", None) != "assistant":
                continue
            metadata = getattr(turn, "metadata_json", {}) or {}
            document_review = metadata.get("document_review")
            if not isinstance(document_review, dict):
                turn_record = metadata.get("turn_record")
                if isinstance(turn_record, dict):
                    document_review = turn_record.get("document_review")
            conflict = self._primary_high_claim_conflict(
                {"document_review": document_review}
            )
            if conflict is None:
                continue
            if self._claim_conflict_fingerprint(conflict) == target_fingerprint:
                count += 1
        return count

    def _claim_conflict_fingerprint(self, conflict: dict[str, Any]) -> tuple:
        return (
            conflict.get("conflict_type"),
            tuple(self._normalized_string_list(conflict.get("field_paths"))),
            tuple(self._normalized_string_list(conflict.get("document_ids"))),
            tuple(self._normalized_string_list(conflict.get("evidence_refs"))),
        )

    def _normalized_string_list(self, values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        normalized = [
            value.strip()
            for value in values
            if isinstance(value, str) and value.strip()
        ]
        return sorted(set(normalized))

    def _primary_high_claim_conflict(
        self,
        capability_tool_outputs: dict[str, Any],
    ) -> dict[str, Any] | None:
        document_review = capability_tool_outputs.get("document_review")
        if not isinstance(document_review, dict):
            return None
        for conflict in document_review.get("claim_conflicts", []) or []:
            if not isinstance(conflict, dict):
                continue
            if conflict.get("severity") != "high":
                continue
            if conflict.get("conflict_type") != "claim_vs_document":
                continue
            return conflict
        return None

    def _first_score_risk_code(self, score: ScoreState) -> str | None:
        for risk_flag in score.risk_flags:
            code = self._runtime_text(risk_flag.code)
            if code:
                return code
        return None

    def _sync_last_turn_decision_trace(
        self,
        trace_entries: list[RuntimeTraceEntry],
        action: InterviewNextAction,
    ) -> None:
        for entry in reversed(trace_entries):
            if entry.node_name != "turn_decision":
                continue
            entry.summary = f"decision={action.decision}"
            entry.turn_decision = action.decision
            metadata = dict(entry.metadata or {})
            metadata.update(
                {
                    "requested_documents": list(action.requested_documents),
                    "focus_kind": action.focus_kind,
                    "focus_document_type": action.focus_document_type,
                    "decision_source": "runtime_convergence_guard",
                    "convergence_reason": action.reason,
                }
            )
            entry.metadata = metadata
            return

    def _runtime_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

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
