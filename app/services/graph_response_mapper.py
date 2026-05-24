from __future__ import annotations

from typing import Any

from app.domain.agent_runtime import DS160GraphState, GraphEvent, GraphRunResult
from app.domain.contracts import GovernorDecision, InterviewStateStatus
from app.domain.document_types import normalize_document_type
from app.platform.turn_record import TurnRecord


class GraphResponseMapper:
    """Project graph facts into the legacy response contract."""

    def to_message_response(
        self,
        state: DS160GraphState,
        events: list[GraphEvent],
    ) -> dict[str, Any]:
        final_response = state.final_response
        if final_response is None:
            raise ValueError("graph state must include final_response before mapping")
        self._validate_event_run_ids(state, events)

        requested_documents = self._normalize_document_types(
            final_response.requested_documents
        )
        remaining_required_documents = list(requested_documents)
        current_focus = self._build_current_focus(
            final_response=final_response,
            requested_documents=requested_documents,
            case_state=state.case_state,
        )
        advisory_context = self._build_advisory_context(state.case_state)
        document_review = dict(state.material_review or {})
        prompt_trace = self._build_prompt_trace(
            state=state,
            events=events,
            final_response=final_response,
        )
        runtime_view_state = self._build_runtime_view_state(
            final_response=final_response,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            current_focus=current_focus,
            advisory_context=advisory_context,
            document_review=document_review,
            prompt_trace=prompt_trace,
        )
        turn_decision = self._build_turn_decision(
            final_response=final_response,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            runtime_view_state=runtime_view_state,
        )
        graph_trace = self._build_graph_trace(state, events, final_response)
        turn_record = self._build_turn_record(
            state=state,
            final_response=final_response,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            current_focus=current_focus,
            advisory_context=advisory_context,
            document_review=document_review,
            events=events,
        )

        return {
            "assistant_message": final_response.assistant_message,
            "governor_decision": final_response.decision,
            "score_summary": self._score_summary_from_case_state(state.case_state),
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "gate_progress": self._payload(state.case_state.get("gate_progress")),
            "turn_decision": turn_decision,
            "document_review": document_review,
            "advisory_context": advisory_context,
            "prompt_trace": prompt_trace,
            "runtime_view_state": runtime_view_state,
            "turn_record": turn_record,
            "agent_runtime": "graph",
            "graph_run_id": state.run_id,
            "graph_trace": graph_trace,
        }

    def _build_current_focus(
        self,
        *,
        final_response: GraphRunResult,
        requested_documents: list[str],
        case_state: dict[str, Any],
    ) -> dict[str, Any]:
        decision = final_response.decision
        existing_focus = self._payload(case_state.get("current_focus"))
        if decision == GovernorDecision.NEED_MORE_EVIDENCE.value:
            return {
                "owner": "graph_runtime",
                "kind": "required_document",
                "document_type": requested_documents[0] if requested_documents else None,
            }
        if decision == GovernorDecision.ROUTE_CORRECTION.value:
            return {
                "owner": "graph_runtime",
                "kind": "route_correction",
                "question": final_response.assistant_message,
            }
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return {
                "owner": "graph_runtime",
                "kind": "risk_review",
                "risk_code": (
                    self._string_or_none(existing_focus.get("risk_code"))
                    or self._first_risk_code(case_state)
                ),
            }
        if decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return {
                "owner": "graph_runtime",
                "kind": "refusal",
                "risk_code": (
                    self._string_or_none(existing_focus.get("risk_code"))
                    or self._first_risk_code(case_state)
                ),
                "reason": final_response.assistant_message,
            }
        return {
            "owner": "graph_runtime",
            "kind": "interview_question",
            "question": final_response.assistant_message,
        }

    def _build_turn_decision(
        self,
        *,
        final_response: GraphRunResult,
        requested_documents: list[str],
        remaining_required_documents: list[str],
        runtime_view_state: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "decision": final_response.decision,
            "assistant_message_author": final_response.assistant_message_author,
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "focus_kind": runtime_view_state["current_focus"].get("kind"),
            "focus_document_type": runtime_view_state.get("current_key_proof"),
            "focus_risk_code": runtime_view_state.get("current_risk_code"),
            "governor_decision": final_response.decision,
            "guard_status": final_response.guard_status,
            "incomplete_reason": final_response.incomplete_reason,
            "next_safe_action": final_response.next_safe_action,
            "current_key_question": runtime_view_state.get("current_key_question"),
            "current_key_proof": runtime_view_state.get("current_key_proof"),
            "current_risk_code": runtime_view_state.get("current_risk_code"),
        }

    def _build_runtime_view_state(
        self,
        *,
        final_response: GraphRunResult,
        requested_documents: list[str],
        remaining_required_documents: list[str],
        current_focus: dict[str, Any],
        advisory_context: dict[str, Any],
        document_review: dict[str, Any],
        prompt_trace: dict[str, Any],
    ) -> dict[str, Any]:
        current_key_question = self._string_or_none(current_focus.get("question"))
        current_key_proof = self._string_or_none(current_focus.get("document_type"))
        current_risk_code = self._string_or_none(current_focus.get("risk_code"))
        public_status = self._derive_public_status(
            decision=final_response.decision,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
        )
        risk_level = (
            self._string_or_none(advisory_context.get("risk_level"))
            or self._derive_risk_level(public_status)
        )
        return {
            "source_turn_id": None,
            "decision": final_response.decision,
            "governor_decision": final_response.decision,
            "public_status": public_status,
            "risk_level": risk_level,
            "current_focus": current_focus,
            "current_key_question": current_key_question,
            "current_key_proof": current_key_proof,
            "current_risk_code": current_risk_code,
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "allowed_next_actions": self._derive_allowed_next_actions(
                public_status=public_status,
                current_key_question=current_key_question,
                current_key_proof=current_key_proof,
            ),
            "advisory_context": advisory_context,
            "document_review": document_review,
            "prompt_trace": prompt_trace,
        }

    def _build_turn_record(
        self,
        *,
        state: DS160GraphState,
        final_response: GraphRunResult,
        requested_documents: list[str],
        remaining_required_documents: list[str],
        current_focus: dict[str, Any],
        advisory_context: dict[str, Any],
        document_review: dict[str, Any],
        events: list[GraphEvent],
    ) -> dict[str, Any]:
        user_turn = self._payload(state.user_turn)
        trace_refs = self._trace_refs(events)
        artifacts = [
            {"kind": "requested_document", "document_type": document_type}
            for document_type in requested_documents
        ]
        artifacts.extend(
            {
                "kind": "citation",
                "citation_id": citation_id,
            }
            for citation_id in final_response.used_citation_ids
        )
        artifacts.append(
            {
                "kind": "graph_run",
                "run_id": state.run_id,
                "assistant_message_author": final_response.assistant_message_author,
                "guard_status": final_response.guard_status,
            }
        )
        return TurnRecord.create(
            session_id=state.session_id,
            user_turn_id=(
                self._string_or_none(user_turn.get("turn_id")) or state.client_turn_id
            ),
            user_input=self._string_or_none(user_turn.get("content")) or "",
            decision=final_response.decision,
            assistant_message=final_response.assistant_message,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            focus=current_focus,
            trace_refs=trace_refs,
            artifacts=artifacts,
            advisory_summary=self._build_turn_advisory_summary(advisory_context),
            document_review=document_review,
        ).model_dump(mode="json", exclude_none=True)

    def _build_prompt_trace(
        self,
        *,
        state: DS160GraphState,
        events: list[GraphEvent],
        final_response: GraphRunResult,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "prompt_pack_id": "ds160.graph_runtime",
            "prompt_version": state.schema_version,
            "graph_run_id": state.run_id,
            "assistant_message_author": final_response.assistant_message_author,
            "guard_status": final_response.guard_status,
        }
        for event in reversed(events):
            if event.event_type != "adjudication_completed":
                continue
            event_payload = self._payload(event.payload)
            for key in ("provider", "model", "reasoning_effort"):
                value = self._string_or_none(event_payload.get(key))
                if value is not None:
                    payload[key] = value
            break
        return payload

    def _build_graph_trace(
        self,
        state: DS160GraphState,
        events: list[GraphEvent],
        final_response: GraphRunResult,
    ) -> dict[str, Any]:
        return {
            "schema_version": state.schema_version,
            "run_id": state.run_id,
            "event_count": len(events),
            "event_types": [event.event_type for event in events],
            "guard_status": final_response.guard_status,
            "incomplete_reason": final_response.incomplete_reason,
            "assistant_message_author": final_response.assistant_message_author,
            "used_citation_ids": list(final_response.used_citation_ids),
            "citation_count": len(state.citation_bundle.citations),
            "public_claim_count": len(final_response.public_claims),
        }

    def _build_advisory_context(
        self,
        case_state: dict[str, Any],
    ) -> dict[str, Any]:
        interviewer_state = self._payload(case_state.get("interviewer_state"))
        advisory = self._payload(interviewer_state.get("advisory_context"))
        if advisory:
            return advisory

        latest_score = self._latest_payload(case_state.get("score_history_tail"))
        risk_flags = latest_score.get("risk_flags", [])
        risk_codes = [
            item.get("code")
            for item in risk_flags
            if isinstance(item, dict) and self._string_or_none(item.get("code"))
        ]
        return {
            "score_summary": self._score_summary_from_case_state(case_state),
            "risk_codes": risk_codes,
            "missing_evidence": [
                item
                for item in latest_score.get("missing_evidence", [])
                if isinstance(item, str) and item.strip()
            ],
            "risk_level": self._risk_level_from_codes(risk_codes),
        }

    def _score_summary_from_case_state(
        self,
        case_state: dict[str, Any],
    ) -> dict[str, int]:
        advisory = self._payload(
            self._payload(case_state.get("interviewer_state")).get("advisory_context")
        )
        score_summary = self._payload(advisory.get("score_summary"))
        if score_summary:
            return {
                key: int(value)
                for key, value in score_summary.items()
                if isinstance(value, int)
            }

        latest_score = self._latest_payload(case_state.get("score_history_tail"))
        return {
            key: int(latest_score.get(key, 0))
            for key in (
                "category_fit",
                "document_readiness",
                "narrative_consistency",
                "confidence",
            )
            if isinstance(latest_score.get(key, 0), int)
        }

    def _build_turn_advisory_summary(
        self,
        advisory_context: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "risk_codes": list(advisory_context.get("risk_codes", []) or []),
            "missing_evidence": list(
                advisory_context.get("missing_evidence", []) or []
            ),
            "risk_level": advisory_context.get("risk_level"),
        }

    def _trace_refs(self, events: list[GraphEvent]) -> list[str]:
        refs: list[str] = []
        for event in events:
            node = self._string_or_none(self._payload(event.payload).get("node"))
            refs.append(node or event.event_type)
        return refs

    def _validate_event_run_ids(
        self,
        state: DS160GraphState,
        events: list[GraphEvent],
    ) -> None:
        mismatched = [event.run_id for event in events if event.run_id != state.run_id]
        if mismatched:
            raise ValueError("graph events must belong to the mapped graph run")

    def _derive_public_status(
        self,
        *,
        decision: str,
        current_key_proof: str | None,
        current_risk_code: str | None,
    ) -> str:
        if decision == GovernorDecision.SIMULATED_REFUSAL.value:
            return InterviewStateStatus.SIMULATED_REFUSAL.value
        if decision == GovernorDecision.HIGH_RISK_REVIEW.value:
            return InterviewStateStatus.HIGH_RISK_REVIEW.value
        if current_key_proof is not None:
            return InterviewStateStatus.WAITING_KEY_PROOF.value
        if decision in {
            GovernorDecision.NEED_MORE_EVIDENCE.value,
            GovernorDecision.ROUTE_CORRECTION.value,
        }:
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        if current_risk_code is not None:
            return InterviewStateStatus.VERIFY_KEY_ISSUE.value
        return InterviewStateStatus.CONTINUE_INTERVIEW.value

    def _derive_allowed_next_actions(
        self,
        *,
        public_status: str,
        current_key_question: str | None,
        current_key_proof: str | None,
    ) -> list[str]:
        if public_status == InterviewStateStatus.CONTINUE_INTERVIEW.value:
            return ["answer_question", "continue_interview"]
        if public_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            return ["answer_question", "clarify_key_issue"]
        if public_status == InterviewStateStatus.WAITING_KEY_PROOF.value:
            actions = ["upload_key_proof", "explain_missing_proof"]
            if current_key_question:
                actions.insert(0, "answer_question")
            return actions
        if public_status == InterviewStateStatus.HIGH_RISK_REVIEW.value:
            actions = ["wait_for_review"]
            if current_key_proof:
                actions.insert(0, "upload_key_proof")
            return actions
        return ["review_refusal_result"]

    def _derive_risk_level(self, public_status: str) -> str:
        if public_status in {
            InterviewStateStatus.HIGH_RISK_REVIEW.value,
            InterviewStateStatus.SIMULATED_REFUSAL.value,
        }:
            return "high"
        if public_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            return "medium"
        return "none"

    def _first_risk_code(self, case_state: dict[str, Any]) -> str | None:
        advisory = self._build_advisory_context(case_state)
        risk_codes = advisory.get("risk_codes", [])
        if not isinstance(risk_codes, list) or not risk_codes:
            return None
        return self._string_or_none(risk_codes[0])

    def _risk_level_from_codes(self, risk_codes: list[Any]) -> str:
        if risk_codes:
            return "high"
        return "none"

    def _normalize_document_types(self, value: list[str]) -> list[str]:
        normalized: list[str] = []
        for item in value:
            document_type = normalize_document_type(item) or item.strip()
            if document_type and document_type not in normalized:
                normalized.append(document_type)
        return normalized

    def _latest_payload(self, value: Any) -> dict[str, Any]:
        if not isinstance(value, list) or not value:
            return {}
        return self._payload(value[-1])

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
