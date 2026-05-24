from __future__ import annotations

import re
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
            latest_user_message=message_text,
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
            latest_user_message=reason,
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
        latest_user_message: str = "",
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
        pre_style_action = action
        action = self._polish_window_interview_action(
            record,
            action,
            history_turns=history_turns,
            latest_user_message=latest_user_message,
        )
        if action != pre_style_action:
            self._sync_last_turn_decision_trace(
                trace_entries,
                action,
                decision_source="runtime_window_style_guard",
            )
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
            self._sync_last_turn_decision_trace(
                trace_entries,
                action,
                decision_source="runtime_convergence_guard",
            )
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
        conflicts = [item for item in [*cross_document_conflicts, *claim_conflicts]]
        high_conflict = next(
            (
                item
                for item in conflicts
                if self._is_confirmed_high_review_conflict(item)
            ),
            None,
        )
        high_cross_document_conflict = next(
            (
                item
                for item in cross_document_conflicts
                if self._is_confirmed_high_review_conflict(item)
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
        return InterviewNextAction(
            decision=GovernorDecision.HIGH_RISK_REVIEW.value,
            assistant_message=self._review_conflict_window_message(high_conflict),
            requested_documents=[],
            focus_kind="risk_review",
            focus_document_type=None,
            focus_risk_code="record_conflict",
            reason="document_review_high_risk",
        )

    def _is_confirmed_high_review_conflict(self, conflict: dict[str, Any]) -> bool:
        if conflict.get("severity") != "high":
            return False
        summary = (self._runtime_text(conflict.get("summary")) or "").casefold()
        has_material_anchor = bool(
            self._normalized_string_list(conflict.get("document_ids"))
            or self._normalized_string_list(conflict.get("evidence_refs"))
        )
        if self._looks_like_unverified_missing_evidence(summary):
            return False if not has_material_anchor else True
        conflict_type = self._runtime_text(conflict.get("conflict_type"))
        if conflict_type in {"document_vs_document", "claim_vs_document"}:
            return True
        if conflict_type != "missing_verification":
            return False
        if not self._normalized_string_list(conflict.get("document_ids")):
            return False
        field_paths = self._normalized_string_list(conflict.get("field_paths"))
        if {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(set(field_paths)):
            return True
        shortfall_markers = (
            "低于",
            "不足",
            "无法覆盖",
            "不能覆盖",
            "below",
            "less than",
            "shortfall",
            "insufficient",
            "cannot cover",
        )
        return any(marker in summary for marker in shortfall_markers)

    def _looks_like_unverified_missing_evidence(self, summary: str) -> bool:
        if not summary:
            return False
        missing_markers = (
            "no funding proof",
            "no sponsor",
            "not provided",
            "has not been provided",
            "missing",
            "unverified",
            "not verified",
            "awaiting",
            "缺少",
            "未提供",
            "未提交",
            "未验证",
            "待验证",
            "待补",
        )
        return any(marker in summary for marker in missing_markers)

    def _review_conflict_window_message(self, conflict: dict[str, Any]) -> str:
        conflict_type = self._runtime_text(conflict.get("conflict_type"))
        field_paths = set(self._normalized_string_list(conflict.get("field_paths")))
        if conflict_type == "claim_vs_document":
            return "你的说法和材料不一致，请解释。"
        if {
            "/education/first_year_cost",
            "/funding/available_funds",
        }.issubset(field_paths):
            return "资金证明低于 I-20 费用，请解释。"
        if conflict_type == "document_vs_document":
            return "两份材料信息不一致，请解释。"
        return "材料核验有关键冲突，请解释。"

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
            assistant_message="这份材料我看到了。你这次赴美学习什么项目？",
            requested_documents=[],
            focus_kind="interview_question",
            focus_document_type=None,
            focus_risk_code=None,
            reason="cleared_ready_document_request_after_material_change",
        )

    def _polish_window_interview_action(
        self,
        record: SessionRecord,
        action: InterviewNextAction,
        *,
        history_turns: list[Any],
        latest_user_message: str,
    ) -> InterviewNextAction:
        if action.focus_kind != "interview_question":
            return action
        if action.decision != GovernorDecision.CONTINUE_INTERVIEW.value:
            return action

        message = self._strip_coaching_phrases(action.assistant_message)
        if self._should_advance_from_repeated_f1_project_detail(
            record=record,
            action_message=message,
            latest_user_message=latest_user_message,
        ):
            message = self._next_f1_window_checkpoint(history_turns)

        if message == action.assistant_message:
            return action
        return action.model_copy(
            update={
                "assistant_message": message,
                "reason": action.reason or "window_interview_style_guard",
            }
        )

    def _strip_coaching_phrases(self, message: str) -> str:
        normalized = message.strip()
        replacements = (
            "我听到了。",
            "我听到了，",
            "我明白。",
            "我明白，",
            "我理解。",
            "我理解，",
            "我知道了。",
            "我知道了，",
            "好，我记下了。",
            "好，我记下了，",
            "好。那",
            "好，那",
            "那么，",
            "具体一点，",
            "再具体一点，",
            "请具体一点，",
            "这个回答太笼统。",
            "这个回答太笼统，",
            "回答太笼统。",
            "回答太笼统，",
        )
        changed = True
        while changed:
            changed = False
            for phrase in replacements:
                if normalized.startswith(phrase):
                    normalized = normalized[len(phrase) :].strip()
                    changed = True
        normalized = re.sub(
            r"^(?:具体一点|再具体一点|请具体一点|这个回答太笼统|回答太笼统)[，,。.\s]*",
            "",
            normalized,
        )
        return normalized or message.strip()

    def _should_advance_from_repeated_f1_project_detail(
        self,
        *,
        record: SessionRecord,
        action_message: str,
        latest_user_message: str,
    ) -> bool:
        if record.declared_family != "f1":
            return False
        if not self._is_vague_project_answer(latest_user_message):
            return False
        if not self._is_narrow_project_detail_question(action_message):
            return False
        current_question = self._runtime_text((record.current_focus_json or {}).get("question")) or ""
        if not self._is_narrow_project_detail_question(current_question):
            return False
        return self._question_topic(action_message) == self._question_topic(current_question)

    def _is_vague_project_answer(self, message_text: str) -> bool:
        normalized = message_text.strip().lower()
        if not normalized:
            return False
        vague_markers = (
            "很厉害",
            "很强",
            "很好",
            "不错",
            "有名",
            "排名",
            "专业好",
            "专业很",
            "学校好",
            "学校很",
            "相关的",
            "相关工作",
            "差不多",
            "应该",
            "good",
            "great",
            "strong",
            "famous",
            "ranking",
            "related",
        )
        concrete_markers = (
            "课程",
            "课题",
            "实验室",
            "导师",
            "研究",
            "数据",
            "算法",
            "机器学习",
            "统计",
            "论文",
            "实习",
            "岗位",
            "教师",
            "讲师",
            "course",
            "research",
            "lab",
            "professor",
            "algorithm",
            "machine learning",
            "internship",
            "lecturer",
        )
        has_vague_marker = any(marker in normalized for marker in vague_markers)
        has_concrete_marker = any(marker in normalized for marker in concrete_markers)
        return has_vague_marker and not has_concrete_marker

    def _is_narrow_project_detail_question(self, question: str) -> bool:
        normalized = question.lower()
        narrow_markers = (
            "哪门课",
            "哪一门课",
            "哪项训练",
            "哪一项训练",
            "哪一部分",
            "哪部分",
            "什么课程",
            "什么训练",
            "which course",
            "what course",
            "which training",
        )
        project_markers = ("项目", "专业", "program", "major")
        career_markers = ("回国", "毕业", "任教", "工作", "career", "teach", "job")
        return (
            any(marker in normalized for marker in narrow_markers)
            and any(marker in normalized for marker in project_markers)
            and any(marker in normalized for marker in career_markers)
        )

    def _question_topic(self, question: str) -> str | None:
        normalized = question.lower()
        if any(marker in normalized for marker in ("学费", "生活费", "资金", "资助", "fund", "sponsor")):
            return "funding"
        if any(marker in normalized for marker in ("回国", "毕业", "工作", "岗位", "任教", "career", "job")):
            return "post_study_plan"
        if any(marker in normalized for marker in ("学校", "项目", "专业", "i-20", "program", "major")):
            return "program"
        return None

    def _next_f1_window_checkpoint(self, history_turns: list[Any]) -> str:
        transcript = " ".join(
            str(getattr(turn, "content", "") or "")
            for turn in history_turns
            if getattr(turn, "role", None) == "assistant"
        )
        if not self._contains_any(transcript, ("本科", "成绩", "语言", "academic", "gpa", "toefl", "ielts")):
            return "你本科读的是什么专业？"
        if not self._contains_any(transcript, ("毕业后", "回国", "工作", "岗位", "任教", "career", "job")):
            return "毕业后你准备做什么工作？"
        if not self._contains_any(transcript, ("学费", "生活费", "资金", "资助", "fund", "sponsor")):
            return "第一年的学费和生活费由谁支付？"
        return "你回国后准备申请什么岗位？"

    def _contains_any(self, value: str, markers: tuple[str, ...]) -> bool:
        normalized = value.lower()
        return any(marker in normalized for marker in markers)

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
        return InterviewNextAction(
            decision=GovernorDecision.HIGH_RISK_REVIEW.value,
            assistant_message=self._review_conflict_window_message(conflict),
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
        *,
        decision_source: str,
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
                    "decision_source": decision_source,
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
