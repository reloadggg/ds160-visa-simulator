from __future__ import annotations

from collections import defaultdict
from typing import Any

from sqlalchemy.orm import Session

from app.db.models import SessionRecord
from app.domain.contracts import GovernorDecision, InterviewStateStatus
from app.platform.runtime_ledger import (
    LedgerEvent,
    LedgerEventType,
    RuntimeViewState,
    SessionLedger,
    TurnLedger,
)
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository


class RuntimeLedgerService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)

    def build_session_ledger(self, session_id: str) -> SessionLedger:
        record = self.session_repo.get(session_id)
        if record is None:
            raise LookupError(f"Session not found: {session_id}")
        turns = self.session_turn_repo.list_session_turns(session_id)
        return self.build_from_record(record, turns=turns)

    def build_from_record(
        self,
        record: SessionRecord,
        *,
        turns: list[Any] | None = None,
    ) -> SessionLedger:
        session_turns = turns if turns is not None else self.session_turn_repo.list_session_turns(record.session_id)
        events = self._build_events(record, session_turns)
        event_ids_by_turn: dict[str, list[str]] = defaultdict(list)
        for event in events:
            if event.turn_id:
                event_ids_by_turn[event.turn_id].append(event.event_id)

        turn_entries = [
            TurnLedger(
                turn_id=turn.turn_id,
                turn_index=turn.turn_index,
                session_id=turn.session_id,
                role=turn.role,
                content=turn.content,
                source=turn.source,
                metadata=dict(turn.metadata_json or {}),
                turn_record=self._turn_record_from_metadata(turn.metadata_json),
                event_ids=list(event_ids_by_turn.get(turn.turn_id, [])),
            )
            for turn in session_turns
        ]
        return SessionLedger(
            session_id=record.session_id,
            phase_state=record.phase_state,
            declared_family=record.declared_family,
            current_governor_decision=record.current_governor_decision,
            current_focus=dict(record.current_focus_json or {}),
            interviewer_state=dict(record.interviewer_state_json or {}),
            turns=turn_entries,
            events=events,
        )

    def scorer_payloads(self, ledger: SessionLedger) -> list[dict[str, Any]]:
        return [
            dict(event.payload)
            for event in ledger.events
            if event.event_type == LedgerEventType.SCORER
        ]

    def trace_payloads(self, ledger: SessionLedger) -> list[dict[str, Any]]:
        return [
            dict(event.payload)
            for event in ledger.events
            if event.event_type == LedgerEventType.TRACE
        ]

    def boundary_payloads(self, ledger: SessionLedger) -> list[dict[str, Any]]:
        return [
            dict(event.payload)
            for event in ledger.events
            if event.event_type == LedgerEventType.BOUNDARY
        ]

    def events_for_turn(
        self,
        ledger: SessionLedger,
        turn_id: str,
    ) -> list[dict[str, Any]]:
        return [
            event.model_dump(mode="json")
            for event in ledger.events
            if event.turn_id == turn_id
        ]

    def latest_view_state(
        self,
        ledger: SessionLedger,
        *,
        fallback_governor_decision: str | None = None,
    ) -> RuntimeViewState:
        latest_turn = self._latest_assistant_turn(ledger)
        latest_boundary = self._latest_event_payload(ledger, LedgerEventType.BOUNDARY)
        governor_decision = (
            self._string_or_none(latest_boundary.get("decision"))
            or self._string_or_none(ledger.current_governor_decision)
            or self._string_or_none(fallback_governor_decision)
            or GovernorDecision.NEED_MORE_EVIDENCE.value
        )
        if latest_turn is None:
            return RuntimeViewState(
                source_turn_id=None,
                decision=governor_decision,
                governor_decision=governor_decision,
                current_focus=dict(ledger.current_focus or {}),
                prompt_trace=self._prompt_trace_payload(ledger),
            )

        metadata_view_state = self._runtime_view_state_from_metadata(
            latest_turn.metadata,
            latest_turn.turn_id,
            fallback_governor_decision=governor_decision,
        )
        if metadata_view_state is not None:
            return metadata_view_state

        turn_record = dict(latest_turn.turn_record or {})
        focus = self._focus_from_turn_record(turn_record) or dict(ledger.current_focus or {})
        decision = (
            self._string_or_none(turn_record.get("decision"))
            or governor_decision
        )
        requested_documents = self._requested_documents_from_turn_record(
            turn_record,
            focus,
        )
        remaining_required_documents = self._remaining_required_documents_from_turn_record(
            turn_record,
            requested_documents,
        )
        advisory_context = self._latest_event_payload(ledger, LedgerEventType.ADVISORY)
        if not advisory_context:
            advisory_context = self._advisory_summary_from_turn_record(turn_record)
        prompt_trace = self._prompt_trace_payload(ledger)
        document_review = self._document_review_from_turn_record(
            turn_record,
            ledger.interviewer_state,
        )
        current_key_question = self._string_or_none(focus.get("question"))
        current_key_proof = (
            self._string_or_none(focus.get("document_type"))
            or (requested_documents[0] if requested_documents else None)
        )
        advisory_risk_codes = advisory_context.get("risk_codes", [])
        current_risk_code = self._string_or_none(focus.get("risk_code"))
        if current_risk_code is None and advisory_risk_codes:
            current_risk_code = self._string_or_none(advisory_risk_codes[0])
        public_status = self._derive_public_status(
            decision=decision,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
        )
        risk_level = (
            self._string_or_none(advisory_context.get("risk_level"))
            or self._derive_risk_level(public_status)
        )
        return RuntimeViewState(
            source_turn_id=latest_turn.turn_id if latest_turn else None,
            decision=decision,
            governor_decision=governor_decision,
            public_status=public_status,
            risk_level=risk_level,
            current_focus=focus,
            current_key_question=current_key_question,
            current_key_proof=current_key_proof,
            current_risk_code=current_risk_code,
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            allowed_next_actions=self._derive_allowed_next_actions(
                public_status=public_status,
                current_key_question=current_key_question,
                current_key_proof=current_key_proof,
            ),
            advisory_context=advisory_context,
            document_review=document_review,
            prompt_trace=prompt_trace,
        )

    def _build_events(
        self,
        record: SessionRecord,
        turns: list[Any],
    ) -> list[LedgerEvent]:
        events: list[LedgerEvent] = []
        assistant_turns = [turn for turn in turns if getattr(turn, "role", None) == "assistant"]
        trace_groups = self._group_runtime_trace(record.runtime_trace_json or [])

        max_turn_batches = max(
            len(assistant_turns),
            len(trace_groups),
            len(record.score_history_json or []),
            len(record.governor_history_json or []),
        )

        for batch_index in range(max_turn_batches):
            turn = assistant_turns[batch_index] if batch_index < len(assistant_turns) else None
            turn_id = getattr(turn, "turn_id", None)
            turn_index = getattr(turn, "turn_index", None)
            trace_group = trace_groups[batch_index] if batch_index < len(trace_groups) else []
            for trace_index, trace_payload in enumerate(trace_group):
                events.append(
                    LedgerEvent(
                        event_id=self._event_id(turn_id, "trace", batch_index, trace_index),
                        session_id=record.session_id,
                        turn_id=turn_id,
                        turn_index=turn_index,
                        event_type=LedgerEventType.TRACE,
                        source="runtime_trace",
                        name=str(trace_payload.get("node_name") or "runtime_trace"),
                        payload=trace_payload,
                    )
                )
                node_name = str(trace_payload.get("node_name") or "")
                if node_name in {"decide_capability", "resolve_capability"}:
                    events.append(
                        LedgerEvent(
                            event_id=self._event_id(
                                turn_id,
                                "capability",
                                batch_index,
                                trace_index,
                            ),
                            session_id=record.session_id,
                            turn_id=turn_id,
                            turn_index=turn_index,
                            event_type=LedgerEventType.CAPABILITY,
                            source="runtime_trace",
                            name=node_name,
                            payload=trace_payload,
                        )
                    )
                    continue
                tool_calls = trace_payload.get("tool_calls", [])
                if isinstance(tool_calls, list) and tool_calls:
                    events.append(
                        LedgerEvent(
                            event_id=self._event_id(
                                turn_id,
                                "capability",
                                batch_index,
                                trace_index,
                            ),
                            session_id=record.session_id,
                            turn_id=turn_id,
                            turn_index=turn_index,
                            event_type=LedgerEventType.CAPABILITY,
                            source="runtime_trace",
                            name=str(trace_payload.get("node_name") or "capability"),
                            payload={
                                "node_name": node_name,
                                "provider": trace_payload.get("provider"),
                                "model": trace_payload.get("model"),
                                "tool_calls": tool_calls,
                            },
                        )
                    )

            if batch_index < len(record.score_history_json or []):
                score_payload = dict((record.score_history_json or [])[batch_index] or {})
                events.append(
                    LedgerEvent(
                        event_id=self._event_id(turn_id, "scorer", batch_index),
                        session_id=record.session_id,
                        turn_id=turn_id,
                        turn_index=turn_index,
                        event_type=LedgerEventType.SCORER,
                        source="score_history",
                        name=str(score_payload.get("scoring_stage") or "score_history"),
                        payload=score_payload,
                    )
                )

            if batch_index < len(record.governor_history_json or []):
                governor_payload = dict((record.governor_history_json or [])[batch_index] or {})
                events.append(
                    LedgerEvent(
                        event_id=self._event_id(turn_id, "boundary", batch_index),
                        session_id=record.session_id,
                        turn_id=turn_id,
                        turn_index=turn_index,
                        event_type=LedgerEventType.BOUNDARY,
                        source="governor_history",
                        name=str(governor_payload.get("decision") or "governor_history"),
                        payload=governor_payload,
                    )
                )

            if turn is not None:
                events.extend(
                    self._graph_trace_events_from_metadata(
                        record,
                        turn,
                        batch_index,
                    )
                )
                advisory_summary = self._advisory_summary_from_metadata(turn.metadata_json)
                if advisory_summary:
                    events.append(
                        LedgerEvent(
                            event_id=self._event_id(turn_id, "advisory", batch_index),
                            session_id=record.session_id,
                            turn_id=turn_id,
                            turn_index=turn_index,
                            event_type=LedgerEventType.ADVISORY,
                            source="turn_record",
                            name="advisory_summary",
                            payload=advisory_summary,
                        )
                    )
        return events

    def _graph_trace_events_from_metadata(
        self,
        record: SessionRecord,
        turn: Any,
        batch_index: int,
    ) -> list[LedgerEvent]:
        metadata = dict(getattr(turn, "metadata_json", None) or {})
        graph_events = metadata.get("graph_events")
        if not isinstance(graph_events, list):
            return []

        ledger_events: list[LedgerEvent] = []
        for index, raw_event in enumerate(graph_events):
            if not isinstance(raw_event, dict):
                continue
            event_type = self._string_or_none(raw_event.get("event_type"))
            if event_type is None:
                continue
            payload = dict(raw_event.get("payload") or {})
            payload.update(
                {
                    "graph_event_type": event_type,
                    "graph_run_id": raw_event.get("run_id"),
                    "sequence": raw_event.get("sequence"),
                    "schema_version": raw_event.get("schema_version"),
                }
            )
            ledger_events.append(
                LedgerEvent(
                    event_id=self._event_id(
                        getattr(turn, "turn_id", None),
                        "graph-trace",
                        batch_index,
                        index,
                    ),
                    session_id=record.session_id,
                    turn_id=getattr(turn, "turn_id", None),
                    turn_index=getattr(turn, "turn_index", None),
                    event_type=LedgerEventType.TRACE,
                    source="graph_events",
                    name=event_type,
                    payload=payload,
                )
            )
        return ledger_events

    def _runtime_view_state_from_metadata(
        self,
        metadata: dict[str, Any],
        turn_id: str,
        *,
        fallback_governor_decision: str,
    ) -> RuntimeViewState | None:
        payload = metadata.get("runtime_view_state")
        if not isinstance(payload, dict) or not payload:
            return None
        if payload.get("source_turn_id") != turn_id:
            return None
        candidate = dict(payload)
        candidate["decision"] = (
            self._string_or_none(candidate.get("decision"))
            or fallback_governor_decision
        )
        candidate["governor_decision"] = (
            self._string_or_none(candidate.get("governor_decision"))
            or self._string_or_none(candidate.get("decision"))
            or fallback_governor_decision
        )
        return RuntimeViewState.model_validate(candidate)

    def _group_runtime_trace(self, runtime_trace_json: list[Any]) -> list[list[dict[str, Any]]]:
        groups: list[list[dict[str, Any]]] = []
        current_group: list[dict[str, Any]] = []
        for raw_entry in runtime_trace_json:
            payload = dict(raw_entry or {})
            current_group.append(payload)
            if payload.get("node_name") == "turn_decision":
                groups.append(current_group)
                current_group = []
        if current_group:
            groups.append(current_group)
        return groups

    def _turn_record_from_metadata(
        self,
        metadata_json: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        metadata = dict(metadata_json or {})
        turn_record = metadata.get("turn_record")
        if not isinstance(turn_record, dict) or not turn_record:
            return None
        return dict(turn_record)

    def _advisory_summary_from_metadata(
        self,
        metadata_json: dict[str, Any] | None,
    ) -> dict[str, Any] | None:
        turn_record = self._turn_record_from_metadata(metadata_json)
        if not turn_record:
            return None
        advisory_summary = turn_record.get("advisory_summary")
        if not isinstance(advisory_summary, dict) or not advisory_summary:
            return None
        return dict(advisory_summary)

    def _event_id(
        self,
        turn_id: str | None,
        kind: str,
        batch_index: int,
        item_index: int | None = None,
    ) -> str:
        base = turn_id or "session-orphan"
        if item_index is None:
            return f"{base}:{kind}:{batch_index}"
        return f"{base}:{kind}:{batch_index}:{item_index}"

    def _latest_assistant_turn(
        self,
        ledger: SessionLedger,
    ) -> TurnLedger | None:
        for turn in reversed(ledger.turns):
            if turn.role == "assistant":
                return turn
        return None

    def _latest_event_payload(
        self,
        ledger: SessionLedger,
        event_type: LedgerEventType,
    ) -> dict[str, Any]:
        for event in reversed(ledger.events):
            if event.event_type == event_type:
                return dict(event.payload)
        return {}

    def _prompt_trace_payload(
        self,
        ledger: SessionLedger,
    ) -> dict[str, Any]:
        for event in reversed(ledger.events):
            if event.event_type != LedgerEventType.TRACE:
                continue
            if event.name != "turn_decision":
                continue
            payload = dict(event.payload)
            metadata = payload.get("metadata", {})
            reasoning_effort = None
            if isinstance(metadata, dict):
                reasoning_effort = self._string_or_none(metadata.get("reasoning_effort"))
            payload = {
                "prompt_pack_id": self._string_or_none(payload.get("prompt_pack_id")),
                "prompt_version": self._string_or_none(payload.get("prompt_version")),
                "provider": self._string_or_none(payload.get("provider")),
                "model": self._string_or_none(payload.get("model")),
                "reasoning_effort": reasoning_effort,
            }
            if any(value is not None for value in payload.values()):
                return payload
            return {}
        return {}

    def _focus_from_turn_record(
        self,
        turn_record: dict[str, Any],
    ) -> dict[str, Any]:
        focus = turn_record.get("focus")
        if not isinstance(focus, dict) or not focus:
            return {}
        return dict(focus)

    def _advisory_summary_from_turn_record(
        self,
        turn_record: dict[str, Any],
    ) -> dict[str, Any]:
        advisory_summary = turn_record.get("advisory_summary")
        if not isinstance(advisory_summary, dict) or not advisory_summary:
            return {}
        return dict(advisory_summary)

    def _requested_documents_from_turn_record(
        self,
        turn_record: dict[str, Any],
        focus: dict[str, Any],
    ) -> list[str]:
        requested_documents = turn_record.get("requested_documents")
        if isinstance(requested_documents, list):
            return [
                document_type.strip()
                for document_type in requested_documents
                if isinstance(document_type, str) and document_type.strip()
            ]
        focus_document_type = self._string_or_none(focus.get("document_type"))
        if focus_document_type:
            return [focus_document_type]
        return []

    def _remaining_required_documents_from_turn_record(
        self,
        turn_record: dict[str, Any],
        requested_documents: list[str],
    ) -> list[str]:
        remaining_required_documents = turn_record.get("remaining_required_documents")
        if isinstance(remaining_required_documents, list):
            return [
                item.strip()
                for item in remaining_required_documents
                if isinstance(item, str) and item.strip()
            ]
        return list(requested_documents)

    def _document_review_from_turn_record(
        self,
        turn_record: dict[str, Any],
        interviewer_state: dict[str, Any],
    ) -> dict[str, Any]:
        document_review = turn_record.get("document_review")
        if isinstance(document_review, dict) and document_review:
            return dict(document_review)
        if isinstance(interviewer_state, dict):
            payload = interviewer_state.get("document_review")
            if isinstance(payload, dict):
                return dict(payload)
        return {}

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

    def _derive_risk_level(
        self,
        public_status: str,
    ) -> str:
        if public_status in {
            InterviewStateStatus.HIGH_RISK_REVIEW.value,
            InterviewStateStatus.SIMULATED_REFUSAL.value,
        }:
            return "high"
        if public_status == InterviewStateStatus.VERIFY_KEY_ISSUE.value:
            return "medium"
        return "none"

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value:
            return None
        return value
