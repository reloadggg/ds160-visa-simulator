from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    GovernorDecision,
    InterviewRiskLevel,
    InterviewStateStatus,
    RiskFlag,
    ScoreState,
)
from app.domain.runtime import (
    InterviewAllowedNextAction,
    InterviewStateSnapshot,
    PromptTrace,
    RuntimeTraceEntry,
    TurnAdvisoryContext,
)
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.governor_service import (
    DIRECT_REFUSAL_REASON_CODES,
    GovernorService,
)
from app.services.interview_runtime_service import InterviewRuntimeService

FUNDING_QUESTION_MARKERS = (
    "fund",
    "funding",
    "pay",
    "tuition",
    "sponsor",
    "sponsoring",
    "financial",
    "bank",
    "education costs",
    "education expenses",
    "资助",
    "学费",
    "资金",
    "银行",
)
SCHOOL_QUESTION_MARKERS = (
    "school admitted",
    "which school",
    "which university",
    "admitted",
    "admission",
    "university",
    "college",
    "program",
    "education history",
    "education background",
    "academic background",
    "i-20",
    "sevis",
    "学校",
    "录取",
    "项目",
    "专业",
)
TRAVEL_PURPOSE_QUESTION_MARKERS = (
    "purpose of your travel",
    "why are you traveling",
    "why do you want to study",
    "purpose of your trip",
    "travel purpose",
    "目的",
    "赴美",
    "旅行目的",
)
HIGH_RISK_REVIEW_REASON_CODES = (
    "record_conflict",
    "unresolved_key_proof_gap",
    "evasive_answer",
)


class InterviewerRuntimeService:
    def __init__(self, db: Session | Any) -> None:
        self.db = db
        self.interview_runtime = InterviewRuntimeService(db)
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.governor = GovernorService()

    def run_turn(self, record: SessionRecord, message_text: str) -> dict:
        history_turns = self.session_turn_repo.list_session_turns(record.session_id)
        history_turn_count = max(len(history_turns) - 1, 0)
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
        profile_json = profile.model_dump(mode="json")
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
        requested_documents = self._select_requested_documents(
            record,
            score,
            action.decision,
            action,
            governor.get("requested_documents", []),
        )
        advisory_context = self._build_advisory_context(score)
        prompt_trace = self._extract_prompt_trace(trace_entries)
        response = self._action_to_response(
            action,
            score,
            requested_documents,
            advisory_context,
            prompt_trace,
        )
        risk_codes = self._extract_risk_codes(score)
        current_focus = self._build_current_focus(
            action,
            requested_documents,
            risk_codes,
            refusal_reason=(
                response["assistant_message"]
                if action.decision == GovernorDecision.SIMULATED_REFUSAL.value
                else None
            ),
        )
        final_decision = action.decision

        record.profile_json = profile_json
        record.current_governor_decision = final_decision
        record.current_focus_json = current_focus
        record.phase_state = self._derive_phase_state(
            turn_decision=final_decision,
        )
        record.interviewer_state_json = self._build_interviewer_state(
            final_decision,
            governor["decision"],
            response["decision_hint"],
            current_focus,
            score,
            history_turn_count,
            advisory_context=advisory_context,
            prompt_trace=prompt_trace,
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
            "assistant_message": response["assistant_message"],
            "governor_decision": response["governor_decision"],
            "score_summary": dict(response["score_summary"]),
            "requested_documents": list(response["requested_documents"]),
            "turn_decision": dict(response["turn_decision"]),
            "advisory_context": dict(response["advisory_context"]),
            "prompt_trace": dict(response["prompt_trace"]),
        }

    def _derive_phase_state(
        self,
        *,
        turn_decision: str,
    ) -> str:
        if turn_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return "session_closed"
        return "interview"

    def _build_current_focus(
        self,
        action: InterviewNextAction,
        requested_documents: list[str],
        risk_codes: list[str],
        *,
        refusal_reason: str | None = None,
    ) -> dict[str, str | None]:
        decision = action.decision
        if action.focus_kind == "interview_question" or decision == GovernorDecision.CONTINUE_INTERVIEW.value:
            return {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": action.assistant_message,
            }
        if action.focus_kind == "required_document" or decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            return {
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": action.focus_document_type
                or (action.requested_documents[0] if action.requested_documents else None)
                or (requested_documents[0] if requested_documents else None),
            }
        if action.focus_kind == "route_correction" or decision == GovernorDecision.ROUTE_CORRECTION.value:
            return {
                "owner": "interviewer_runtime_service",
                "kind": "route_correction",
                "question": action.assistant_message,
            }
        if action.focus_kind == "risk_review" or decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return {
                "owner": "interviewer_runtime_service",
                "kind": "risk_review",
                "risk_code": action.focus_risk_code or (risk_codes[0] if risk_codes else None),
            }
        if action.focus_kind == "refusal" or decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return {
                "owner": "interviewer_runtime_service",
                "kind": "refusal",
                "risk_code": action.focus_risk_code or (risk_codes[0] if risk_codes else None),
                "reason": refusal_reason or action.reason or action.assistant_message,
            }
        return {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": action.assistant_message,
        }

    def _build_interviewer_state(
        self,
        decision: str,
        governor_decision: str,
        decision_hint: str,
        current_focus: dict[str, str | None],
        score: ScoreState,
        history_turn_count: int,
        *,
        advisory_context: TurnAdvisoryContext,
        prompt_trace: PromptTrace,
    ) -> dict[str, Any]:
        risk_codes = self._extract_risk_codes(score)
        current_key_question = current_focus.get("question")
        current_key_proof = self._current_key_proof(current_focus, score)
        current_risk_code = current_focus.get("risk_code") or (risk_codes[0] if risk_codes else None)
        state_status = self._derive_interview_state_status(
            decision,
            current_key_proof,
            current_risk_code,
        )
        allowed_next_actions = self._allowed_next_actions(
            state_status,
            current_key_question=current_key_question,
            current_key_proof=current_key_proof,
        )
        snapshot = InterviewStateSnapshot(
            status=state_status,
            public_status=state_status,
            decision=decision,
            governor_decision=governor_decision,
            next_action=allowed_next_actions[0].value,
            decision_hint=decision_hint,
            current_key_question=current_key_question,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
            risk_level=self._derive_risk_level(score),
            allowed_next_actions=allowed_next_actions,
            requested_documents=self._requested_documents(current_focus),
            risk_codes=risk_codes,
            history_turn_count=history_turn_count,
        )
        payload = snapshot.model_dump(mode="json")
        payload["advisory_context"] = advisory_context.model_dump(mode="json")
        payload["prompt_trace"] = prompt_trace.model_dump(
            mode="json",
            exclude_none=True,
        )
        return payload

    def _derive_interview_state_status(
        self,
        governor_decision: str,
        current_key_proof: str | None,
        current_risk_code: str | None,
    ) -> InterviewStateStatus:
        if governor_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewStateStatus.SIMULATED_REFUSAL
        if governor_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewStateStatus.HIGH_RISK_REVIEW
        if current_key_proof is not None:
            return InterviewStateStatus.WAITING_KEY_PROOF
        if governor_decision in {
            GovernorDecision.NEED_MORE_EVIDENCE.value,
            GovernorDecision.ROUTE_CORRECTION.value,
        }:
            return InterviewStateStatus.VERIFY_KEY_ISSUE
        if current_risk_code is not None:
            return InterviewStateStatus.VERIFY_KEY_ISSUE
        return InterviewStateStatus.CONTINUE_INTERVIEW

    def _derive_risk_level(self, score: ScoreState) -> InterviewRiskLevel:
        severities = {risk_flag.severity for risk_flag in score.risk_flags}
        if "high" in severities:
            return InterviewRiskLevel.HIGH
        if "medium" in severities:
            return InterviewRiskLevel.MEDIUM
        if "low" in severities:
            return InterviewRiskLevel.LOW
        return InterviewRiskLevel.NONE

    def _current_key_proof(
        self,
        current_focus: dict[str, str | None],
        score: ScoreState,
    ) -> str | None:
        del score
        document_type = current_focus.get("document_type")
        if document_type is not None:
            return document_type
        return None

    def _requested_documents(
        self,
        current_focus: dict[str, str | None],
    ) -> list[str]:
        document_type = current_focus.get("document_type")
        if document_type is None:
            return []
        return [document_type]

    def _allowed_next_actions(
        self,
        state_status: InterviewStateStatus,
        *,
        current_key_question: str | None,
        current_key_proof: str | None,
    ) -> list[InterviewAllowedNextAction]:
        if state_status == InterviewStateStatus.CONTINUE_INTERVIEW:
            return [
                InterviewAllowedNextAction.ANSWER_QUESTION,
                InterviewAllowedNextAction.CONTINUE_INTERVIEW,
            ]
        if state_status == InterviewStateStatus.VERIFY_KEY_ISSUE:
            return [
                InterviewAllowedNextAction.ANSWER_QUESTION,
                InterviewAllowedNextAction.CLARIFY_KEY_ISSUE,
            ]
        if state_status == InterviewStateStatus.WAITING_KEY_PROOF:
            allowed = [
                InterviewAllowedNextAction.UPLOAD_KEY_PROOF,
                InterviewAllowedNextAction.EXPLAIN_MISSING_PROOF,
            ]
            if current_key_question:
                allowed.insert(0, InterviewAllowedNextAction.ANSWER_QUESTION)
            return allowed
        if state_status == InterviewStateStatus.HIGH_RISK_REVIEW:
            allowed = [InterviewAllowedNextAction.WAIT_FOR_REVIEW]
            if current_key_proof:
                allowed.insert(0, InterviewAllowedNextAction.UPLOAD_KEY_PROOF)
            return allowed
        return [InterviewAllowedNextAction.REVIEW_REFUSAL_RESULT]

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
        governor = self.governor.decide(
            profile,
            score,
            self._build_early_term_candidate(
                record.declared_family,
                findings or [],
            ),
        )
        review_signal = self._high_risk_review_signal(profile, score)
        if (
            governor["decision"] != GovernorDecision.SIMULATED_REFUSAL.value
            and review_signal is not None
        ):
            governor = {
                "decision": GovernorDecision.HIGH_RISK_REVIEW.value,
                "blocked_actions": ["high_risk_review_signal"],
                "rationale_refs": list(review_signal.evidence_refs),
                "requested_documents": list(governor.get("requested_documents", [])),
            }
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

    def _action_to_response(
        self,
        action: InterviewNextAction,
        score: ScoreState,
        requested_documents: list[str],
        advisory_context: TurnAdvisoryContext,
        prompt_trace: PromptTrace,
    ) -> dict[str, Any]:
        assistant_message = action.assistant_message
        if action.decision == GovernorDecision.SIMULATED_REFUSAL.value:
            assistant_message = self._public_refusal_message(score)
        requested_documents = list(requested_documents)
        return {
            "assistant_message": assistant_message,
            "governor_decision": action.decision,
            "score_summary": {},
            "requested_documents": requested_documents,
            "decision_hint": action.decision_hint or action.decision,
            "turn_decision": action.model_dump(mode="json"),
            "advisory_context": advisory_context.model_dump(mode="json"),
            "prompt_trace": prompt_trace.model_dump(
                mode="json",
                exclude_none=True,
            ),
        }

    def _extract_risk_codes(self, score: ScoreState) -> list[str]:
        return [risk_flag.code for risk_flag in score.risk_flags]

    def _select_requested_documents(
        self,
        record: SessionRecord,
        score: ScoreState,
        turn_decision: str,
        action: InterviewNextAction,
        governor_requested_documents: list[str],
    ) -> list[str]:
        explicit_requested_documents = self._normalize_requested_documents(
            action.requested_documents
        )
        if turn_decision != GovernorDecision.NEED_MORE_EVIDENCE.value:
            return explicit_requested_documents[:1]
        if explicit_requested_documents:
            return explicit_requested_documents[:1]
        if action.focus_document_type and action.focus_document_type.strip():
            return [action.focus_document_type.strip()]
        normalized_governor_documents = self._normalize_requested_documents(
            governor_requested_documents
        )
        if normalized_governor_documents:
            return normalized_governor_documents[:1]
        current_focus = record.current_focus_json or {}
        focus_document = current_focus.get("document_type")
        if isinstance(focus_document, str) and focus_document.strip():
            return [focus_document.strip()]
        for document_type in score.missing_evidence:
            if isinstance(document_type, str) and document_type.strip():
                return [document_type.strip()]
        return []

    def _normalize_requested_documents(self, document_types: list[str]) -> list[str]:
        return [
            document_type.strip()
            for document_type in document_types
            if document_type.strip()
        ]

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

    def _build_advisory_context(self, score: ScoreState) -> TurnAdvisoryContext:
        missing_evidence = list(score.missing_evidence)
        return TurnAdvisoryContext(
            score_summary={
                "category_fit": score.category_fit,
                "document_readiness": score.document_readiness,
                "narrative_consistency": score.narrative_consistency,
                "confidence": score.confidence,
            },
            risk_codes=self._extract_risk_codes(score),
            missing_evidence=missing_evidence,
            risk_level=self._derive_risk_level(score),
            missing_evidence_summary=(
                ", ".join(missing_evidence) if missing_evidence else None
            ),
        )

    def _extract_prompt_trace(
        self,
        trace_entries: list[RuntimeTraceEntry],
    ) -> PromptTrace:
        turn_trace = next(
            (
                entry
                for entry in reversed(trace_entries)
                if entry.node_name == "turn_decision"
            ),
            None,
        )
        if turn_trace is None:
            return PromptTrace()
        metadata = turn_trace.metadata if isinstance(turn_trace.metadata, dict) else {}
        return PromptTrace(
            prompt_pack_id=turn_trace.prompt_pack_id,
            prompt_version=turn_trace.prompt_version,
            provider=turn_trace.provider,
            model=turn_trace.model,
            reasoning_effort=metadata.get("reasoning_effort"),
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
        risk_watch = profile.ds160_view.get("risk_watch", {})
        evasive_turn_count = int(risk_watch.get("evasive_turn_count", 0))
        missing_key_proof_turn_count = int(
            risk_watch.get("missing_key_proof_turn_count", 0)
        )

        for reason_code in HIGH_RISK_REVIEW_REASON_CODES:
            risk_flag = next(
                (
                    item
                    for item in score.risk_flags
                    if item.code == reason_code
                    and item.severity == "high"
                    and item.evidence_refs
                ),
                None,
            )
            if risk_flag is None:
                continue
            if reason_code == "record_conflict":
                return risk_flag
            if reason_code == "evasive_answer" and evasive_turn_count >= 2:
                return risk_flag
            if (
                reason_code == "unresolved_key_proof_gap"
                and missing_key_proof_turn_count >= 2
            ):
                return risk_flag
        return None

    def _public_refusal_message(self, score: ScoreState) -> str:
        refusal_codes = {risk_flag.code for risk_flag in score.risk_flags}
        if {"hard_conflict", "fraud_admission"} & refusal_codes:
            return (
                "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，"
                "本次会话到此结束。"
            )
        return "当前记录已形成模拟拒签结果，本次会话到此结束。"

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
        risk_watch = dict(profile.ds160_view.get("risk_watch", {}))
        evasive_turn_count = int(risk_watch.get("evasive_turn_count", 0))
        missing_key_proof_turn_count = int(
            risk_watch.get("missing_key_proof_turn_count", 0)
        )

        current_focus = record.current_focus_json or {}
        focus_kind = current_focus.get("kind")
        focus_question = current_focus.get("question")
        focus_document = current_focus.get("document_type")

        if focus_kind == "interview_question" and focus_question:
            if self._is_evasive_answer(focus_question, message_text):
                evasive_turn_count += 1
            else:
                evasive_turn_count = 0

        if focus_kind == "required_document" and focus_document:
            if focus_document in score.missing_evidence:
                missing_key_proof_turn_count += 1
                if self._is_evasive_document_response(focus_document, message_text):
                    evasive_turn_count += 1
                else:
                    evasive_turn_count = 0
            else:
                missing_key_proof_turn_count = 0
                evasive_turn_count = 0

        risk_watch["evasive_turn_count"] = evasive_turn_count
        risk_watch["missing_key_proof_turn_count"] = missing_key_proof_turn_count
        profile.ds160_view["risk_watch"] = risk_watch

        latest_user_ref = self._latest_user_turn_ref(history_turns)
        if evasive_turn_count >= 2:
            self._upsert_risk_flag(
                score,
                code="evasive_answer",
                severity="high",
                status="supported",
                evidence_refs=[] if latest_user_ref is None else [latest_user_ref],
            )

        if missing_key_proof_turn_count >= 2:
            self._upsert_risk_flag(
                score,
                code="unresolved_key_proof_gap",
                severity="high",
                status="supported",
                evidence_refs=[] if latest_user_ref is None else [latest_user_ref],
            )

    def _upsert_risk_flag(
        self,
        score: ScoreState,
        *,
        code: str,
        severity: str,
        status: str,
        evidence_refs: list[str],
    ) -> None:
        for risk_flag in score.risk_flags:
            if risk_flag.code != code:
                continue
            risk_flag.severity = severity
            risk_flag.status = status
            risk_flag.evidence_refs = list(evidence_refs)
            return

        score.risk_flags.append(
            RiskFlag(
                code=code,
                severity=severity,
                status=status,
                evidence_refs=list(evidence_refs),
            )
        )

    def _latest_user_turn_ref(self, history_turns: list[Any]) -> str | None:
        for turn in reversed(history_turns):
            if getattr(turn, "role", None) != "user":
                continue
            turn_id = getattr(turn, "turn_id", None)
            if isinstance(turn_id, str) and turn_id:
                return f"msg:{turn_id}"
        return None

    def _is_evasive_answer(
        self,
        focus_question: str,
        message_text: str,
    ) -> bool:
        normalized = message_text.lower()
        evasive_markers = (
            "later",
            "not now",
            "move on",
            "another question",
            "school plan",
            "my major",
            "explain later",
            "let's talk about",
            "以后再说",
            "先不说",
            "换个问题",
            "学校计划",
            "专业",
        )
        if any(marker in normalized for marker in evasive_markers):
            return True

        question_topic = self._question_topic(focus_question)
        if question_topic == "funding":
            return not self._mentions_funding(message_text)
        if question_topic == "school":
            return not self._mentions_school_context(message_text)
        if question_topic == "travel_purpose":
            return not self._mentions_travel_purpose(message_text)
        return False

    def _question_topic(self, focus_question: str) -> str | None:
        normalized = focus_question.lower()
        if any(marker in normalized for marker in FUNDING_QUESTION_MARKERS):
            return "funding"
        if any(marker in normalized for marker in SCHOOL_QUESTION_MARKERS):
            return "school"
        if any(marker in normalized for marker in TRAVEL_PURPOSE_QUESTION_MARKERS):
            return "travel_purpose"
        return None

    def _is_evasive_document_response(
        self,
        focus_document: str,
        message_text: str,
    ) -> bool:
        normalized = message_text.lower()
        if any(token in normalized for token in ("upload", "provide", "submit", "proof", "document", "上传", "提供", "提交", "证明", "材料")):
            return False
        if focus_document == "funding_proof":
            return not self._mentions_funding(message_text)
        if focus_document == "passport_bio":
            return "passport" not in normalized
        return True

    def _mentions_funding(self, message_text: str) -> bool:
        normalized = message_text.lower()
        funding_markers = (
            "parent",
            "parents",
            "mother",
            "father",
            "mom",
            "dad",
            "myself",
            "self",
            "self-funded",
            "self funded",
            "sponsor",
            "sponsoring",
            "uncle",
            "aunt",
            "scholarship",
            "bank",
            "savings",
            "financial",
            "funding",
            "pay",
            "cover",
            "tuition",
            "资助",
            "学费",
            "父母",
            "奖学金",
            "自己",
            "银行",
        )
        return any(marker in normalized for marker in funding_markers)

    def _mentions_school_context(self, message_text: str) -> bool:
        normalized = message_text.lower()
        school_markers = (
            "school",
            "university",
            "college",
            "program",
            "admit",
            "admission",
            "i-20",
            "sevis",
            "学校",
            "录取",
            "项目",
            "专业",
        )
        return any(marker in normalized for marker in school_markers)

    def _mentions_travel_purpose(self, message_text: str) -> bool:
        normalized = message_text.lower()
        purpose_markers = (
            "study",
            "student",
            "school",
            "degree",
            "education",
            "program",
            "学",
            "留学",
            "读书",
        )
        return any(marker in normalized for marker in purpose_markers)
