from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.agents.schemas import InterviewNextAction
from app.db.models import SessionRecord
from app.domain.contracts import (
    GovernorDecision,
    InterviewStateStatus,
    ScoreState,
)
from app.domain.runtime import (
    InterviewAllowedNextAction,
    InterviewStateSnapshot,
    PromptTrace,
    RuntimeTraceEntry,
    TurnAdvisoryContext,
)
from app.platform.turn_record import TurnRecord
from app.services.advisory_review_service import AdvisoryReviewService


@dataclass
class InterviewerTurnProjection:
    response: dict[str, Any]
    current_focus: dict[str, Any]
    interviewer_state: dict[str, Any]
    phase_state: str
    turn_record: dict[str, Any]


class InterviewerTurnProjectorService:
    def __init__(self) -> None:
        self.advisory_review = AdvisoryReviewService()

    def project(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        action: InterviewNextAction,
        score: ScoreState,
        governor_decision: str,
        governor_requested_documents: list[str],
        trace_entries: list[RuntimeTraceEntry],
        history_turn_count: int,
        history_turns: list[Any],
    ) -> InterviewerTurnProjection:
        requested_documents = self.select_requested_documents(
            record=record,
            score=score,
            action=action,
            governor_requested_documents=governor_requested_documents,
        )
        advisory_context = self.advisory_review.build_context(score)
        prompt_trace = self.extract_prompt_trace(trace_entries)
        response = self.action_to_response(
            action=action,
            score=score,
            requested_documents=requested_documents,
            advisory_context=advisory_context,
            prompt_trace=prompt_trace,
        )
        risk_codes = self.advisory_review.extract_risk_codes(score)
        current_focus = self.build_current_focus(
            action=action,
            requested_documents=requested_documents,
            risk_codes=risk_codes,
            refusal_reason=(
                response["assistant_message"]
                if action.decision == GovernorDecision.SIMULATED_REFUSAL.value
                else None
            ),
        )
        interviewer_state = self.build_interviewer_state(
            decision=action.decision,
            governor_decision=governor_decision,
            decision_hint=response["decision_hint"],
            current_focus=current_focus,
            score=score,
            history_turn_count=history_turn_count,
            advisory_context=advisory_context,
            prompt_trace=prompt_trace,
        )
        turn_record = self.build_turn_record(
            record=record,
            message_text=message_text,
            action=action,
            assistant_message=response["assistant_message"],
            requested_documents=requested_documents,
            current_focus=current_focus,
            advisory_context=advisory_context,
            trace_entries=trace_entries,
            history_turns=history_turns,
        )
        return InterviewerTurnProjection(
            response=response,
            current_focus=current_focus,
            interviewer_state=interviewer_state,
            phase_state=self.derive_phase_state(action.decision),
            turn_record=turn_record,
        )

    def derive_phase_state(self, turn_decision: str) -> str:
        if turn_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return "session_closed"
        return "interview"

    def build_current_focus(
        self,
        *,
        action: InterviewNextAction,
        requested_documents: list[str],
        risk_codes: list[str],
        refusal_reason: str | None = None,
    ) -> dict[str, str | None]:
        decision = action.decision
        if (
            action.focus_kind == "interview_question"
            or decision == GovernorDecision.CONTINUE_INTERVIEW.value
        ):
            return {
                "owner": "interviewer_runtime_service",
                "kind": "interview_question",
                "question": action.assistant_message,
            }
        if (
            action.focus_kind == "required_document"
            or decision == GovernorDecision.NEED_MORE_EVIDENCE.value
        ):
            return {
                "owner": "interviewer_runtime_service",
                "kind": "required_document",
                "document_type": action.focus_document_type
                or (action.requested_documents[0] if action.requested_documents else None)
                or (requested_documents[0] if requested_documents else None),
            }
        if (
            action.focus_kind == "route_correction"
            or decision == GovernorDecision.ROUTE_CORRECTION.value
        ):
            return {
                "owner": "interviewer_runtime_service",
                "kind": "route_correction",
                "question": action.assistant_message,
            }
        if (
            action.focus_kind == "risk_review"
            or decision == GovernorDecision.HIGH_RISK_REVIEW.value
        ):
            return {
                "owner": "interviewer_runtime_service",
                "kind": "risk_review",
                "risk_code": action.focus_risk_code or (risk_codes[0] if risk_codes else None),
            }
        if (
            action.focus_kind == "refusal"
            or decision == GovernorDecision.SIMULATED_REFUSAL.value
        ):
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

    def build_interviewer_state(
        self,
        *,
        decision: str,
        governor_decision: str,
        decision_hint: str,
        current_focus: dict[str, str | None],
        score: ScoreState,
        history_turn_count: int,
        advisory_context: TurnAdvisoryContext,
        prompt_trace: PromptTrace,
    ) -> dict[str, Any]:
        risk_codes = self.advisory_review.extract_risk_codes(score)
        current_key_question = current_focus.get("question")
        current_key_proof = self.current_key_proof(current_focus)
        current_risk_code = current_focus.get("risk_code") or (risk_codes[0] if risk_codes else None)
        state_status = self.derive_interview_state_status(
            turn_decision=decision,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
        )
        allowed_next_actions = self.allowed_next_actions(
            state_status=state_status,
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
            risk_level=self.advisory_review.derive_risk_level(score),
            allowed_next_actions=allowed_next_actions,
            requested_documents=self.requested_documents(current_focus),
            risk_codes=risk_codes,
            history_turn_count=history_turn_count,
        )
        payload = snapshot.model_dump(mode="json")
        payload["advisory_context"] = advisory_context.model_dump(mode="json")
        payload["prompt_trace"] = prompt_trace.model_dump(mode="json", exclude_none=True)
        return payload

    def derive_interview_state_status(
        self,
        *,
        turn_decision: str,
        current_key_proof: str | None,
        current_risk_code: str | None,
    ) -> InterviewStateStatus:
        if turn_decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewStateStatus.SIMULATED_REFUSAL
        if turn_decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewStateStatus.HIGH_RISK_REVIEW
        if current_key_proof is not None:
            return InterviewStateStatus.WAITING_KEY_PROOF
        if turn_decision in {
            GovernorDecision.NEED_MORE_EVIDENCE.value,
            GovernorDecision.ROUTE_CORRECTION.value,
        }:
            return InterviewStateStatus.VERIFY_KEY_ISSUE
        if current_risk_code is not None:
            return InterviewStateStatus.VERIFY_KEY_ISSUE
        return InterviewStateStatus.CONTINUE_INTERVIEW

    def current_key_proof(
        self,
        current_focus: dict[str, str | None],
    ) -> str | None:
        document_type = current_focus.get("document_type")
        if document_type is not None:
            return document_type
        return None

    def requested_documents(
        self,
        current_focus: dict[str, str | None],
    ) -> list[str]:
        document_type = current_focus.get("document_type")
        if document_type is None:
            return []
        return [document_type]

    def allowed_next_actions(
        self,
        *,
        state_status: InterviewStateStatus,
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

    def action_to_response(
        self,
        *,
        action: InterviewNextAction,
        score: ScoreState,
        requested_documents: list[str],
        advisory_context: TurnAdvisoryContext,
        prompt_trace: PromptTrace,
    ) -> dict[str, Any]:
        assistant_message = action.assistant_message
        if action.decision == GovernorDecision.SIMULATED_REFUSAL.value:
            assistant_message = self.public_refusal_message(score)
        return {
            "assistant_message": assistant_message,
            "governor_decision": action.decision,
            "score_summary": {},
            "requested_documents": list(requested_documents),
            "decision_hint": action.decision_hint or action.decision,
            "turn_decision": action.model_dump(mode="json"),
            "advisory_context": advisory_context.model_dump(mode="json"),
            "prompt_trace": prompt_trace.model_dump(mode="json", exclude_none=True),
        }

    def select_requested_documents(
        self,
        *,
        record: SessionRecord,
        score: ScoreState,
        action: InterviewNextAction,
        governor_requested_documents: list[str],
    ) -> list[str]:
        explicit_requested_documents = self.normalize_requested_documents(
            action.requested_documents
        )
        if action.decision != GovernorDecision.NEED_MORE_EVIDENCE.value:
            return explicit_requested_documents[:1]
        if explicit_requested_documents:
            return explicit_requested_documents[:1]
        if action.focus_document_type and action.focus_document_type.strip():
            return [action.focus_document_type.strip()]
        normalized_governor_documents = self.normalize_requested_documents(
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

    def normalize_requested_documents(self, document_types: list[str]) -> list[str]:
        return [
            document_type.strip()
            for document_type in document_types
            if document_type.strip()
        ]

    def build_turn_record(
        self,
        *,
        record: SessionRecord,
        message_text: str,
        action: InterviewNextAction,
        assistant_message: str,
        requested_documents: list[str],
        current_focus: dict[str, Any],
        advisory_context: TurnAdvisoryContext,
        trace_entries: list[RuntimeTraceEntry],
        history_turns: list[Any],
    ) -> dict[str, Any]:
        return TurnRecord.create(
            session_id=record.session_id,
            user_turn_id=self.latest_user_turn_id(history_turns),
            user_input=message_text,
            decision=action.decision,
            assistant_message=assistant_message,
            requested_documents=requested_documents,
            focus=current_focus,
            trace_refs=self.build_trace_refs(trace_entries),
            artifacts=self.build_turn_artifacts(requested_documents, current_focus),
            advisory_summary=self.build_turn_advisory_summary(advisory_context),
        ).model_dump(mode="json", exclude_none=True)

    def build_trace_refs(
        self,
        trace_entries: list[RuntimeTraceEntry],
    ) -> list[str]:
        refs: list[str] = []
        for entry in trace_entries:
            node_name = getattr(entry, "node_name", None)
            if not isinstance(node_name, str) or not node_name.strip():
                continue
            refs.append(node_name.strip())
        return refs

    def build_turn_artifacts(
        self,
        requested_documents: list[str],
        current_focus: dict[str, Any],
    ) -> list[dict[str, Any]]:
        artifacts: list[dict[str, Any]] = []
        for document_type in requested_documents:
            if isinstance(document_type, str) and document_type.strip():
                artifacts.append(
                    {
                        "kind": "requested_document",
                        "document_type": document_type.strip(),
                    }
                )
        if current_focus.get("kind") == "risk_review" and current_focus.get("risk_code"):
            artifacts.append(
                {
                    "kind": "risk_focus",
                    "risk_code": current_focus["risk_code"],
                }
            )
        return artifacts

    def build_turn_advisory_summary(
        self,
        advisory_context: TurnAdvisoryContext,
    ) -> dict[str, Any]:
        payload = advisory_context.model_dump(mode="json", exclude_none=True)
        return {
            "risk_codes": list(payload.get("risk_codes", [])),
            "missing_evidence": list(payload.get("missing_evidence", [])),
            "risk_level": payload.get("risk_level"),
        }

    def extract_prompt_trace(
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

    def public_refusal_message(self, score: ScoreState) -> str:
        refusal_codes = {risk_flag.code for risk_flag in score.risk_flags}
        if {"hard_conflict", "fraud_admission"} & refusal_codes:
            return (
                "当前记录已确认存在虚假陈述或伪造材料，系统给出模拟拒签结果，"
                "本次会话到此结束。"
            )
        return "当前记录已形成模拟拒签结果，本次会话到此结束。"

    def latest_user_turn_id(self, history_turns: list[Any]) -> str | None:
        for turn in reversed(history_turns):
            if getattr(turn, "role", None) != "user":
                continue
            turn_id = getattr(turn, "turn_id", None)
            if isinstance(turn_id, str) and turn_id:
                return turn_id
        return None
