from hashlib import sha256
import logging
from typing import Literal

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.models import SessionTurnRecord
from app.domain.runtime import GateOverallStatus
from app.platform.turn_record import TurnRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import (
    DuplicateClientMessageIdError,
    SessionTurnRepository,
)
from app.services.case_memory_service import CaseMemoryService
from app.services.gate_runtime_service import GateRuntimeService
from app.services.interview_memory_service import (
    INTERVIEW_MEMORY_KEY,
    InterviewMemoryService,
)
from app.services.interviewer_runtime_service import InterviewerRuntimeService
from app.services.native_interviewer_runtime_service import (
    NativeInterviewerRuntimeService,
)
from app.services.runtime_view_contract_service import RuntimeViewContractService
from app.services.runtime_errors import ModelRuntimeError
from app.services.session_read_model_service import SessionReadModelService

logger = logging.getLogger(__name__)


class DuplicateTurnInProgressError(RuntimeError):
    def __init__(self, session_id: str, client_message_id: str) -> None:
        self.session_id = session_id
        self.client_message_id = client_message_id
        super().__init__(
            f"Duplicate client_message_id is still being processed: {client_message_id}"
        )


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionClosedError(RuntimeError):
    def __init__(self, session_id: str, detail: str) -> None:
        self.session_id = session_id
        self.detail = detail
        super().__init__(detail)


PublicRuntimeMode = Literal["legacy", "native_interviewer"]


def _explicit_list_field(
    payload: dict,
    key: str,
    *,
    fallback: list[str] | None = None,
) -> list[str]:
    if key in payload:
        return list(payload.get(key) or [])
    return list(fallback or [])


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.gate_runtime = GateRuntimeService(db)
        self.interviewer_runtime = InterviewerRuntimeService(db)
        self.native_interviewer_runtime = NativeInterviewerRuntimeService(db)
        self.session_read_model = SessionReadModelService(db)
        self.case_memory = CaseMemoryService(db)
        self.interview_memory = InterviewMemoryService()

    def handle_user_turn(
        self,
        session_id: str,
        message_text: str,
        *,
        client_message_id: str | None = None,
    ) -> dict:
        committed_user_turn: SessionTurnRecord | None = None
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if self._is_refusal_closed(record):
            raise SessionClosedError(session_id, self._closed_session_detail(record))
        record = self.gate_runtime.refresh_session(session_id, save=False)

        if client_message_id:
            duplicate_response = self._duplicate_turn_response(
                record,
                client_message_id=client_message_id,
            )
            if duplicate_response is not None:
                return duplicate_response
        in_progress_turn = self._latest_unanswered_user_turn(record.session_id)
        if in_progress_turn is not None and (
            not client_message_id
            or in_progress_turn.client_message_id != client_message_id
        ):
            raise DuplicateTurnInProgressError(
                record.session_id,
                in_progress_turn.client_message_id or client_message_id or "",
            )

        try:
            try:
                user_turn = self.session_turn_repo.append_user_turn(
                    session_id=record.session_id,
                    content=message_text,
                    source="user_message",
                    metadata_json=self._user_turn_metadata(
                        record,
                        client_message_id=client_message_id,
                    ),
                    commit=True,
                )
                committed_user_turn = user_turn
            except DuplicateClientMessageIdError as exc:
                duplicate_response = self._duplicate_turn_response(
                    record,
                    client_message_id=exc.client_message_id,
                )
                if duplicate_response is not None:
                    return duplicate_response
                raise DuplicateTurnInProgressError(
                    record.session_id,
                    exc.client_message_id,
                ) from exc
            self._capture_interview_memory(record.session_id, user_turn)
            self._capture_user_turn_claims(record.session_id, user_turn, message_text)
            self.db.commit()
            self.db.refresh(record)
            self.db.refresh(user_turn)

            if record.gate_status_json.get("status") == GateOverallStatus.FAMILY_NOT_SELECTED:
                response = self.gate_runtime.build_gate_response(record)
                self._apply_gate_response_state(
                    record,
                    response,
                    user_input=message_text,
                    user_turn_id=None,
                )
                assistant_turn = self._append_assistant_turn(record, response)
                self._sync_runtime_view_contract(record, response, assistant_turn)
                self.session_repo.save(record)
                return response

            runtime_mode = self._select_public_runtime(record.session_id)
            response = self._run_public_runtime(
                runtime_mode,
                record,
                message_text,
                user_turn,
            )
            assistant_turn = self._append_assistant_turn(record, response)
            self._sync_runtime_view_contract(record, response, assistant_turn)
            self._strip_internal_runtime_fields(response)
            self.session_repo.save(record)
            return response
        except Exception as exc:
            self.db.rollback()
            if committed_user_turn is not None and not self._should_keep_user_turn_on_error(
                exc
            ):
                self._cleanup_incomplete_committed_user_turn(committed_user_turn)
            raise

    def _user_turn_metadata(
        self,
        record,
        *,
        client_message_id: str | None,
    ) -> dict:
        metadata = {"phase_state": record.phase_state}
        if client_message_id:
            metadata["client_message_id"] = client_message_id
        return metadata

    def _duplicate_turn_response(
        self,
        record,
        *,
        client_message_id: str,
    ) -> dict | None:
        user_turn = self.session_turn_repo.find_user_turn_by_client_message_id(
            session_id=record.session_id,
            client_message_id=client_message_id,
        )
        if user_turn is None:
            return None
        assistant_turn = self.session_turn_repo.next_assistant_turn_after(
            session_id=record.session_id,
            user_turn=user_turn,
        )
        if assistant_turn is None:
            raise DuplicateTurnInProgressError(record.session_id, client_message_id)
        response = self._response_from_assistant_turn(record, assistant_turn)
        response["idempotent_replay"] = True
        return response

    def _cleanup_incomplete_committed_user_turn(
        self,
        user_turn: SessionTurnRecord,
    ) -> None:
        try:
            persisted_turn = self.db.get(SessionTurnRecord, user_turn.turn_id)
            if persisted_turn is None:
                return
            session_turns = self.session_turn_repo.list_session_turns(
                persisted_turn.session_id
            )
            has_later_turn = any(
                turn.turn_index > persisted_turn.turn_index for turn in session_turns
            )
            if has_later_turn:
                return
            self.db.delete(persisted_turn)
            self.db.commit()
        except Exception:
            self.db.rollback()
            logger.exception(
                "failed to clean up incomplete committed user turn",
                extra={
                    "session_id": user_turn.session_id,
                    "turn_id": user_turn.turn_id,
                },
            )

    def _should_keep_user_turn_on_error(self, exc: Exception) -> bool:
        return (
            isinstance(exc, ModelRuntimeError)
            and exc.upstream_code == "native_quality_guard_failed"
        )

    def _latest_unanswered_user_turn(
        self,
        session_id: str,
    ) -> SessionTurnRecord | None:
        turns = self.session_turn_repo.list_session_turns(session_id)
        if not turns or turns[-1].role != "user":
            return None
        return turns[-1]

    def _response_from_assistant_turn(
        self,
        record,
        assistant_turn: SessionTurnRecord,
    ) -> dict:
        metadata = dict(assistant_turn.metadata_json or {})
        metadata_runtime_view_state = dict(metadata.get("runtime_view_state", {}) or {})
        if metadata_runtime_view_state.get("source_turn_id") == assistant_turn.turn_id:
            runtime_view_state = metadata_runtime_view_state
        else:
            turns_until_assistant = [
                turn
                for turn in self.session_turn_repo.list_session_turns(record.session_id)
                if turn.turn_index <= assistant_turn.turn_index
            ]
            read_model = self.session_read_model.build_from_record(
                record,
                turns=turns_until_assistant,
            )
            runtime_view_state = RuntimeViewContractService.payload(
                read_model.runtime_view_state,
                anchored_only=True,
            )
        fallback = {
            "governor_decision": metadata.get("governor_decision")
            or record.current_governor_decision,
            "requested_documents": list(metadata.get("requested_documents", []) or []),
            "remaining_required_documents": list(
                metadata.get("remaining_required_documents", []) or []
            ),
            "turn_decision": self._turn_decision_payload_from_metadata(metadata),
            "document_review": dict(metadata.get("document_review", {}) or {}),
            "prompt_trace": dict(metadata.get("prompt_trace", {}) or {}),
            "runtime_view_state": dict(metadata.get("runtime_view_state", {}) or {}),
        }
        if not runtime_view_state:
            runtime_view_state = dict(fallback.get("runtime_view_state") or {})
        response = {
            "assistant_message": assistant_turn.content,
            "governor_decision": RuntimeViewContractService.governor_decision(
                runtime_view_state,
                fallback,
            ),
            "requested_documents": RuntimeViewContractService.requested_documents(
                runtime_view_state,
                fallback,
            ),
            "remaining_required_documents": (
                RuntimeViewContractService.remaining_required_documents(
                    runtime_view_state,
                    fallback,
                )
            ),
            "gate_progress": self.gate_runtime.build_gate_support(record)[
                "gate_progress"
            ],
            "turn_decision": RuntimeViewContractService.turn_decision(
                runtime_view_state,
                fallback,
            ),
            "document_review": RuntimeViewContractService.document_review(
                runtime_view_state,
                fallback,
            ),
            "turn_record": dict(metadata.get("turn_record", {}) or {}),
            "prompt_trace": RuntimeViewContractService.prompt_trace(
                runtime_view_state,
                fallback,
            ),
            "runtime_view_state": runtime_view_state,
        }
        return response

    def _turn_decision_payload_from_metadata(self, metadata: dict) -> dict:
        turn_decision = metadata.get("turn_decision")
        if isinstance(turn_decision, dict):
            return dict(turn_decision)
        if isinstance(turn_decision, str) and turn_decision:
            return {"decision": turn_decision}
        turn_record = dict(metadata.get("turn_record", {}) or {})
        decision = turn_record.get("decision")
        return {"decision": decision} if decision else {}

    def _capture_user_turn_claims(
        self,
        session_id: str,
        user_turn: SessionTurnRecord,
        message_text: str,
    ) -> None:
        claims = self.case_memory.extract_explicit_user_turn_claims(
            turn_id=user_turn.turn_id,
            message_text=message_text,
        )
        if not claims:
            return
        self.case_memory.add_user_turn_claims(
            session_id=session_id,
            turn_id=user_turn.turn_id,
            claims=claims,
        )

    def _capture_interview_memory(
        self,
        session_id: str,
        user_turn: SessionTurnRecord,
    ) -> None:
        assistant_turn = self._previous_assistant_turn(session_id, user_turn)
        memory = self.interview_memory.annotate_user_answer(
            assistant_turn=assistant_turn,
            user_turn=user_turn,
        )
        if not memory:
            return
        metadata = dict(user_turn.metadata_json or {})
        metadata[INTERVIEW_MEMORY_KEY] = memory
        user_turn.metadata_json = metadata
        self.db.add(user_turn)
        self.db.flush()

    def _previous_assistant_turn(
        self,
        session_id: str,
        user_turn: SessionTurnRecord,
    ) -> SessionTurnRecord | None:
        previous_assistant: SessionTurnRecord | None = None
        for turn in self.session_turn_repo.list_session_turns(session_id):
            if turn.turn_index >= user_turn.turn_index:
                break
            if turn.role == "assistant":
                previous_assistant = turn
        return previous_assistant

    def refresh_after_material_change(
        self,
        session_id: str,
        *,
        reason: str,
    ) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if self._is_refusal_closed(record):
            return {}
        record = self.gate_runtime.refresh_session(session_id, save=False)
        if record.gate_status_json.get("status") == GateOverallStatus.FAMILY_NOT_SELECTED:
            return {}

        try:
            runtime_mode = self._select_public_runtime(record.session_id)
            response = self._run_material_change_public_runtime(
                runtime_mode,
                record,
                reason=reason,
            )
            self._sync_material_refresh_response_state(record, response, reason=reason)
            self._strip_internal_runtime_fields(response)
            self.session_repo.save(record)
            return response
        except Exception:
            self.db.rollback()
            raise

    def _is_refusal_closed(self, record) -> bool:
        if record.current_governor_decision == "simulated_refusal":
            return True
        interviewer_state = record.interviewer_state_json or {}
        if interviewer_state.get("status") == "simulated_refusal":
            return True
        return record.phase_state == "session_closed"

    def _select_public_runtime(self, session_id: str) -> PublicRuntimeMode:
        if settings.agent_runtime in {"graph", "graph_shadow", "native_interviewer"}:
            return "native_interviewer"
        if settings.agent_runtime == "graph_canary":
            if self._is_graph_canary_selected(session_id):
                return "native_interviewer"
        return "legacy"

    def _selected_agent_runtime_label(self, runtime_mode: PublicRuntimeMode) -> str:
        if runtime_mode != "native_interviewer":
            return settings.agent_runtime
        if settings.agent_runtime == "native_interviewer":
            return "native_interviewer"
        if settings.agent_runtime == "graph_shadow":
            return "graph_shadow"
        return "graph"

    def _runtime_execution_payload(
        self,
        *,
        requested_public_runtime: PublicRuntimeMode,
        public_runtime: PublicRuntimeMode,
        execution_runtime: str,
        source: str,
        fallback_runtime: str | None = None,
        error: dict | None = None,
    ) -> dict:
        payload = {
            "schema_version": "runtime.execution.v1",
            "configured_runtime": settings.agent_runtime,
            "requested_public_runtime": requested_public_runtime,
            "public_runtime": public_runtime,
            "execution_runtime": execution_runtime,
            "runtime_engine": execution_runtime,
            "source": source,
            "fail_open_to_legacy": False,
        }
        if fallback_runtime:
            payload["fallback_runtime"] = fallback_runtime
        if error:
            payload["error_type"] = error.get("error_type")
            payload["error_message"] = error.get("error_message")
        if (
            settings.agent_runtime in {"graph", "graph_canary", "graph_shadow"}
            and requested_public_runtime == "native_interviewer"
        ):
            payload["compatibility_runtime_label"] = settings.agent_runtime
        return {key: value for key, value in payload.items() if value is not None}

    def _is_graph_canary_selected(self, session_id: str) -> bool:
        percent = settings.agent_runtime_canary_percent
        if percent <= 0:
            return False
        if percent >= 100:
            return True
        bucket = int(sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) % 100
        return bucket < percent

    def _run_public_runtime(
        self,
        runtime_mode: PublicRuntimeMode,
        record,
        message_text: str,
        user_turn: SessionTurnRecord,
    ) -> dict:
        if runtime_mode == "native_interviewer":
            response = self.native_interviewer_runtime.run_turn(
                record,
                message_text,
                user_turn=user_turn,
            )
            response["agent_runtime"] = self._selected_agent_runtime_label(runtime_mode)
            response["selected_public_runtime"] = "native_interviewer"
            response["runtime_execution"] = self._runtime_execution_payload(
                requested_public_runtime="native_interviewer",
                public_runtime="native_interviewer",
                execution_runtime="native_interviewer_runtime",
                source="message_turn",
            )
            self._apply_graph_response_state(record, response)
            return self.gate_runtime.merge_interview_response(response, record)

        interview_response = self.interviewer_runtime.run_turn(record, message_text)
        response = self.gate_runtime.merge_interview_response(interview_response, record)
        response["agent_runtime"] = settings.agent_runtime
        response["selected_public_runtime"] = "legacy"
        response["runtime_execution"] = self._runtime_execution_payload(
            requested_public_runtime="legacy",
            public_runtime="legacy",
            execution_runtime="interviewer_runtime_service",
            source="message_turn",
        )
        return response

    def _run_material_change_public_runtime(
        self,
        runtime_mode: PublicRuntimeMode,
        record,
        *,
        reason: str,
    ) -> dict:
        if runtime_mode == "native_interviewer":
            response = self.native_interviewer_runtime.run_material_change(
                record,
                reason=self._graph_material_change_reason(reason),
            )
            response["agent_runtime"] = self._selected_agent_runtime_label(runtime_mode)
            response["selected_public_runtime"] = "native_interviewer"
            response["runtime_execution"] = self._runtime_execution_payload(
                requested_public_runtime="native_interviewer",
                public_runtime="native_interviewer",
                execution_runtime="native_interviewer_runtime",
                source="material_change",
            )
            self._apply_graph_response_state(record, response)
            return self.gate_runtime.merge_interview_response(response, record)

        response = self._run_legacy_material_change(record, reason=reason)
        response["agent_runtime"] = settings.agent_runtime
        response["selected_public_runtime"] = "legacy"
        response["runtime_execution"] = self._runtime_execution_payload(
            requested_public_runtime="legacy",
            public_runtime="legacy",
            execution_runtime="interviewer_runtime_service",
            source="material_change",
        )
        return response

    def _run_legacy_material_change(self, record, *, reason: str) -> dict:
        interview_response = self.interviewer_runtime.refresh_after_material_change(
            record,
            reason=reason,
        )
        return self.gate_runtime.merge_interview_response(interview_response, record)

    def _graph_material_change_reason(self, reason: str) -> str:
        normalized = reason.strip()
        if normalized.startswith("debug_fill:"):
            document_type = normalized.removeprefix("debug_fill:").strip()
            return f"material_added:{document_type}" if document_type else "material_added"
        if normalized.startswith("debug_material_bundle:"):
            return "materials_updated"
        if normalized.startswith("document_parsed:"):
            return "document_parsed"
        if normalized.startswith("case_understanding:"):
            return "case_understanding"
        return "materials_updated"

    def _strip_internal_runtime_fields(self, response: dict) -> None:
        response.pop("graph_shadow", None)
        response.pop("graph_events", None)
        response.pop("graph_runtime_engine", None)
        response.pop("graph_runtime_engine_class", None)
        response.pop("graph_runtime_error", None)

    def _apply_graph_response_state(self, record, response: dict) -> None:
        decision = response.get("governor_decision") or (
            response.get("turn_decision", {}) or {}
        ).get("decision")
        decision = decision or "continue_interview"
        runtime_view_state = dict(response.get("runtime_view_state", {}) or {})
        current_focus = dict(
            runtime_view_state.get("current_focus")
            or (response.get("turn_record", {}) or {}).get("focus")
            or {}
        )
        record.phase_state = (
            "session_closed" if decision == "simulated_refusal" else "interview"
        )
        record.current_governor_decision = decision
        record.current_focus_json = current_focus
        record.interviewer_state_json = {
            "owner": (
                "native_interviewer_runtime"
                if response.get("selected_public_runtime") == "native_interviewer"
                else "graph_runtime"
            ),
            "status": decision,
            "public_status": runtime_view_state.get("public_status"),
            "decision": decision,
            "governor_decision": decision,
            "next_action": (response.get("turn_decision", {}) or {}).get(
                "next_safe_action"
            ),
            "decision_hint": decision,
            "current_focus": current_focus,
            "current_key_question": runtime_view_state.get("current_key_question"),
            "current_key_proof": runtime_view_state.get("current_key_proof"),
            "current_risk_code": runtime_view_state.get("current_risk_code"),
            "risk_level": runtime_view_state.get("risk_level"),
            "allowed_next_actions": list(
                runtime_view_state.get("allowed_next_actions", []) or []
            ),
            "requested_documents": list(
                response.get("requested_documents", []) or []
            ),
            "remaining_required_documents": list(
                response.get("remaining_required_documents", []) or []
            ),
            "document_review": dict(response.get("document_review", {}) or {}),
            "advisory_context": dict(response.get("advisory_context", {}) or {}),
            "prompt_trace": dict(response.get("prompt_trace", {}) or {}),
            "native_run_id": response.get("native_run_id"),
            "selected_public_runtime": response.get("selected_public_runtime"),
            "runtime_execution": dict(response.get("runtime_execution", {}) or {}),
            "graph_run_id": response.get("graph_run_id"),
            "graph_trace": dict(response.get("graph_trace", {}) or {}),
        }

    def _apply_gate_response_state(
        self,
        record,
        response: dict,
        *,
        user_input: str,
        user_turn_id: str | None,
    ) -> None:
        decision = response.get("governor_decision") or "need_more_evidence"
        record.current_governor_decision = decision
        requested_documents = list(response.get("requested_documents", []) or [])
        remaining_required_documents = _explicit_list_field(
            response,
            "remaining_required_documents",
            fallback=requested_documents,
        )
        if requested_documents:
            record.current_focus_json = {
                "owner": "gate_runtime_service",
                "kind": "required_document",
                "document_type": requested_documents[0],
            }
        else:
            record.current_focus_json = {
                "owner": "gate_runtime_service",
                "kind": "gate_review",
            }
        response["turn_record"] = TurnRecord.create(
            session_id=record.session_id,
            user_turn_id=user_turn_id,
            user_input=user_input,
            decision=decision,
            assistant_message=response.get("assistant_message", ""),
            requested_documents=requested_documents,
            remaining_required_documents=remaining_required_documents,
            focus=record.current_focus_json,
            trace_refs=[],
            artifacts=[
                {"kind": "requested_document", "document_type": document_type}
                for document_type in requested_documents
            ],
        ).model_dump(mode="json", exclude_none=True)

    def _closed_session_detail(self, record) -> str:
        current_focus = record.current_focus_json or {}
        reason = current_focus.get("reason")
        if isinstance(reason, str) and reason.strip():
            return f"{reason.strip()} 当前会话已结束，不能继续提交新的面谈消息。"
        return "当前会话已收到模拟拒签结果，不能继续提交新的面谈消息。"

    def _append_assistant_turn(
        self,
        record,
        response: dict,
    ) -> SessionTurnRecord:
        gate_status = record.gate_status_json.get("status")
        source = (
            "gate_runtime_service"
            if gate_status == GateOverallStatus.FAMILY_NOT_SELECTED
            else "graph_runtime_adapter"
            if response.get("agent_runtime") == "graph"
            and response.get("selected_public_runtime", "graph") == "graph"
            else "native_interviewer_runtime"
            if response.get("selected_public_runtime") == "native_interviewer"
            else "interviewer_runtime_service"
        )
        assistant_turn = self.session_turn_repo.append_assistant_turn(
            session_id=record.session_id,
            content=response["assistant_message"],
            source=source,
            metadata_json={
                "phase_state": record.phase_state,
                "governor_decision": response.get("governor_decision"),
                "turn_decision": (response.get("turn_decision", {}) or {}).get("decision"),
                "current_focus_kind": (record.current_focus_json or {}).get("kind"),
                "prompt_trace": response.get("prompt_trace", {}),
                "agent_runtime": response.get("agent_runtime"),
                "selected_public_runtime": response.get("selected_public_runtime"),
                "native_run_id": response.get("native_run_id"),
                "graph_run_id": response.get("graph_run_id"),
                "graph_trace": response.get("graph_trace"),
                "graph_events": response.get("graph_events"),
                "graph_runtime_error": response.get("graph_runtime_error"),
                "runtime_execution": response.get("runtime_execution"),
            },
            commit=False,
        )
        turn_record = self._finalize_turn_record(response, assistant_turn.turn_id)
        if turn_record is not None:
            assistant_turn.metadata_json = {
                **(assistant_turn.metadata_json or {}),
                "turn_record": turn_record,
            }
        return assistant_turn

    def _sync_runtime_view_contract(
        self,
        record,
        response: dict,
        assistant_turn: SessionTurnRecord,
    ) -> None:
        original_runtime_view_state = dict(response.get("runtime_view_state", {}) or {})
        read_model = self.session_read_model.build_from_record(record)
        runtime_view_state = RuntimeViewContractService.payload(
            read_model.runtime_view_state
        )
        response["governor_decision"] = RuntimeViewContractService.governor_decision(
            runtime_view_state,
            response,
        )
        response["requested_documents"] = RuntimeViewContractService.requested_documents(
            runtime_view_state,
            response,
        )
        response["remaining_required_documents"] = (
            RuntimeViewContractService.remaining_required_documents(
                runtime_view_state,
                response,
            )
        )
        response["turn_decision"] = RuntimeViewContractService.turn_decision(
            runtime_view_state,
            response,
        )
        response["document_review"] = RuntimeViewContractService.document_review(
            runtime_view_state,
            response,
        )
        response["prompt_trace"] = RuntimeViewContractService.prompt_trace(
            runtime_view_state,
            response,
        )
        response["runtime_view_state"] = runtime_view_state
        turn_decision_payload = response.get("turn_decision", {})
        if (
            isinstance(turn_decision_payload, dict)
            and turn_decision_payload.get("governor_decision") is not None
            and turn_decision_payload.get("decision") is not None
        ):
            response["governor_decision"] = turn_decision_payload["decision"]

        metadata = dict(assistant_turn.metadata_json or {})
        current_focus = dict(
            runtime_view_state.get("current_focus")
            or record.current_focus_json
            or {}
        )
        metadata.update(
            {
                "phase_state": read_model.phase_state,
                "governor_decision": response.get("governor_decision"),
                "requested_documents": list(response.get("requested_documents", []) or []),
                "remaining_required_documents": list(
                    response.get("remaining_required_documents", []) or []
                ),
                "turn_decision": (response.get("turn_decision", {}) or {}).get("decision"),
                "current_focus_kind": current_focus.get("kind"),
                "document_review": dict(response.get("document_review", {}) or {}),
                "prompt_trace": dict(response.get("prompt_trace", {}) or {}),
                "runtime_execution": dict(
                    response.get("runtime_execution", {}) or {}
                ),
            }
        )
        if (
            response.get("agent_runtime") == "graph"
            or response.get("selected_public_runtime") == "native_interviewer"
        ):
            graph_runtime_view_state = dict(original_runtime_view_state)
            graph_runtime_view_state["source_turn_id"] = assistant_turn.turn_id
            graph_runtime_view_state["prompt_trace"] = dict(
                response.get("prompt_trace", {}) or {}
            )
            response["runtime_view_state"] = graph_runtime_view_state
            metadata["runtime_view_state"] = graph_runtime_view_state
            metadata["prompt_trace"] = dict(response.get("prompt_trace", {}) or {})
        elif runtime_view_state.get("source_turn_id") == assistant_turn.turn_id:
            metadata["runtime_view_state"] = runtime_view_state
        assistant_turn.metadata_json = metadata

    def _sync_material_refresh_response_state(
        self,
        record,
        response: dict,
        *,
        reason: str,
    ) -> None:
        runtime_view_state = dict(response.get("runtime_view_state", {}) or {})
        if not runtime_view_state:
            runtime_view_state = self._build_material_refresh_runtime_view_state(
                record,
                response,
            )
        response["governor_decision"] = (
            response.get("governor_decision")
            or runtime_view_state.get("governor_decision")
            or record.current_governor_decision
        )
        response["requested_documents"] = _explicit_list_field(
            response,
            "requested_documents",
            fallback=list(runtime_view_state.get("requested_documents", []) or []),
        )
        response["remaining_required_documents"] = _explicit_list_field(
            response,
            "remaining_required_documents",
            fallback=list(
                runtime_view_state.get("remaining_required_documents", []) or []
            ),
        )
        response["turn_decision"] = dict(response.get("turn_decision", {}) or {})
        if not response["turn_decision"] and response.get("governor_decision"):
            response["turn_decision"] = {"decision": response["governor_decision"]}
        response["document_review"] = dict(response.get("document_review", {}) or {})
        response["prompt_trace"] = dict(response.get("prompt_trace", {}) or {})
        response["runtime_view_state"] = runtime_view_state

        refresh_metadata = {
            "reason": reason,
            "sanitized_reason": self._graph_material_change_reason(reason),
            "agent_runtime": response.get("agent_runtime"),
            "selected_public_runtime": response.get("selected_public_runtime"),
            "governor_decision": response.get("governor_decision"),
            "turn_decision": dict(response.get("turn_decision", {}) or {}),
            "prompt_trace": dict(response.get("prompt_trace", {}) or {}),
            "runtime_execution": dict(response.get("runtime_execution", {}) or {}),
            "native_run_id": response.get("native_run_id"),
            "graph_run_id": response.get("graph_run_id"),
            "graph_trace": dict(response.get("graph_trace", {}) or {}),
            "graph_events": list(response.get("graph_events", []) or []),
            "graph_runtime_error": response.get("graph_runtime_error"),
            "runtime_view_state": runtime_view_state,
            "assistant_turn_created": False,
        }
        refresh_metadata = {
            key: value
            for key, value in refresh_metadata.items()
            if value not in ({}, [], None)
        }
        interviewer_state = dict(record.interviewer_state_json or {})
        interviewer_state["last_material_refresh"] = refresh_metadata
        record.interviewer_state_json = interviewer_state
        response["material_refresh"] = {
            **refresh_metadata,
            "assistant_turn_created": False,
        }

    def _build_material_refresh_runtime_view_state(
        self,
        record,
        response: dict,
    ) -> dict:
        interviewer_state = dict(record.interviewer_state_json or {})
        current_focus = dict(record.current_focus_json or {})
        turn_decision = dict(response.get("turn_decision", {}) or {})
        decision = (
            response.get("governor_decision")
            or turn_decision.get("decision")
            or interviewer_state.get("governor_decision")
            or interviewer_state.get("decision")
            or record.current_governor_decision
        )
        requested_documents = _explicit_list_field(
            response,
            "requested_documents",
            fallback=list(interviewer_state.get("requested_documents", []) or []),
        )
        remaining_required_documents = _explicit_list_field(
            response,
            "remaining_required_documents",
            fallback=list(
                interviewer_state.get("remaining_required_documents", []) or []
            ),
        )
        return {
            "source_turn_id": None,
            "decision": decision,
            "governor_decision": decision,
            "public_status": interviewer_state.get("public_status") or decision,
            "risk_level": interviewer_state.get("risk_level"),
            "current_focus": current_focus,
            "current_key_question": (
                interviewer_state.get("current_key_question")
                or current_focus.get("question")
            ),
            "current_key_proof": (
                interviewer_state.get("current_key_proof")
                or current_focus.get("document_type")
                or (requested_documents[0] if requested_documents else None)
            ),
            "current_risk_code": (
                interviewer_state.get("current_risk_code")
                or current_focus.get("risk_code")
            ),
            "requested_documents": requested_documents,
            "remaining_required_documents": remaining_required_documents,
            "allowed_next_actions": list(
                interviewer_state.get("allowed_next_actions", []) or []
            ),
            "advisory_context": dict(
                response.get("advisory_context", {})
                or interviewer_state.get("advisory_context", {})
                or {}
            ),
            "document_review": dict(response.get("document_review", {}) or {}),
            "prompt_trace": dict(response.get("prompt_trace", {}) or {}),
        }

    def _finalize_turn_record(
        self,
        response: dict,
        assistant_turn_id: str,
    ) -> dict | None:
        payload = response.get("turn_record")
        if not isinstance(payload, dict) or not payload:
            return None
        finalized = TurnRecord.model_validate(payload).with_assistant_turn(
            assistant_turn_id
        )
        payload_json = finalized.model_dump(mode="json", exclude_none=True)
        response["turn_record"] = payload_json
        return payload_json
