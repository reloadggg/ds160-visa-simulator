from hashlib import sha256
from typing import Literal

from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.models import SessionTurnRecord
from app.domain.runtime import GateOverallStatus
from app.platform.turn_record import TurnRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.gate_runtime_service import GateRuntimeService
from app.services.graph_runtime_adapter import GraphRuntimeAdapter
from app.services.interviewer_runtime_service import InterviewerRuntimeService
from app.services.runtime_view_contract_service import RuntimeViewContractService
from app.services.session_read_model_service import SessionReadModelService


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class SessionClosedError(RuntimeError):
    def __init__(self, session_id: str, detail: str) -> None:
        self.session_id = session_id
        self.detail = detail
        super().__init__(detail)


PublicRuntimeMode = Literal["legacy", "graph"]


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.gate_runtime = GateRuntimeService(db)
        self.interviewer_runtime = InterviewerRuntimeService(db)
        self.graph_runtime = GraphRuntimeAdapter(db)
        self.session_read_model = SessionReadModelService(db)
        self.case_memory = CaseMemoryService(db)

    def handle_user_turn(self, session_id: str, message_text: str) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if self._is_refusal_closed(record):
            raise SessionClosedError(session_id, self._closed_session_detail(record))
        record = self.gate_runtime.refresh_session(session_id, save=False)

        try:
            user_turn = self.session_turn_repo.append_user_turn(
                session_id=record.session_id,
                content=message_text,
                source="user_message",
                metadata_json={"phase_state": record.phase_state},
                commit=False,
            )
            self._capture_user_turn_claims(record.session_id, user_turn, message_text)

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

            graph_shadow = self._run_graph_shadow(record, message_text, user_turn)
            runtime_mode = self._select_public_runtime(record.session_id)
            response = self._run_public_runtime(
                runtime_mode,
                record,
                message_text,
                user_turn,
                graph_shadow=graph_shadow,
            )
            self._attach_graph_shadow(response, graph_shadow)
            assistant_turn = self._append_assistant_turn(record, response)
            self._sync_runtime_view_contract(record, response, assistant_turn)
            self._strip_internal_runtime_fields(response)
            self.session_repo.save(record)
            return response
        except Exception:
            self.db.rollback()
            raise

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
            graph_shadow = self._run_material_change_graph_shadow(record, reason=reason)
            runtime_mode = self._select_public_runtime(record.session_id)
            response = self._run_material_change_public_runtime(
                runtime_mode,
                record,
                reason=reason,
                graph_shadow=graph_shadow,
            )
            self._attach_graph_shadow(response, graph_shadow)
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
        if settings.agent_runtime == "graph":
            return "graph"
        if settings.agent_runtime == "graph_canary":
            if self._is_graph_canary_selected(session_id):
                return "graph"
        return "legacy"

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
        *,
        graph_shadow: dict | None,
    ) -> dict:
        if runtime_mode == "graph":
            try:
                response = self.graph_runtime.run_turn(
                    record,
                    message_text,
                    user_turn=user_turn,
                )
            except Exception as exc:
                if not settings.agent_runtime_fail_open_to_legacy:
                    raise
                interview_response = self.interviewer_runtime.run_turn(record, message_text)
                response = self.gate_runtime.merge_interview_response(
                    interview_response,
                    record,
                )
                response["graph_runtime_error"] = {
                    "status": "error",
                    "agent_runtime": settings.agent_runtime,
                    "selected_public_runtime": "graph",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                    "fallback_runtime": "legacy",
                }
                return response
            response["selected_public_runtime"] = "graph"
            self._apply_graph_response_state(record, response)
            return self.gate_runtime.merge_interview_response(response, record)

        interview_response = self.interviewer_runtime.run_turn(record, message_text)
        response = self.gate_runtime.merge_interview_response(interview_response, record)
        if graph_shadow:
            response["agent_runtime"] = settings.agent_runtime
            response["selected_public_runtime"] = "legacy"
        return response

    def _run_material_change_public_runtime(
        self,
        runtime_mode: PublicRuntimeMode,
        record,
        *,
        reason: str,
        graph_shadow: dict | None,
    ) -> dict:
        if runtime_mode == "graph":
            try:
                response = self.graph_runtime.run_material_change(
                    record,
                    reason=self._graph_material_change_reason(reason),
                )
            except Exception as exc:
                if not settings.agent_runtime_fail_open_to_legacy:
                    raise
                response = self._run_legacy_material_change(record, reason=reason)
                response["graph_runtime_error"] = {
                    "status": "error",
                    "agent_runtime": settings.agent_runtime,
                    "selected_public_runtime": "graph",
                    "error_type": exc.__class__.__name__,
                    "error_message": str(exc),
                    "fallback_runtime": "legacy",
                }
                return response
            response["selected_public_runtime"] = "graph"
            self._apply_graph_response_state(record, response)
            return self.gate_runtime.merge_interview_response(response, record)

        response = self._run_legacy_material_change(record, reason=reason)
        if graph_shadow:
            response["agent_runtime"] = settings.agent_runtime
            response["selected_public_runtime"] = "legacy"
        return response

    def _run_legacy_material_change(self, record, *, reason: str) -> dict:
        interview_response = self.interviewer_runtime.refresh_after_material_change(
            record,
            reason=reason,
        )
        return self.gate_runtime.merge_interview_response(interview_response, record)

    def _run_graph_shadow(
        self,
        record,
        message_text: str,
        user_turn: SessionTurnRecord,
    ) -> dict | None:
        if settings.agent_runtime != "graph_shadow":
            return None
        if not settings.agent_runtime_trace_enabled:
            return None
        try:
            shadow_response = self.graph_runtime.run_turn(
                record,
                message_text,
                user_turn=user_turn,
            )
        except Exception as exc:
            if not settings.agent_runtime_fail_open_to_legacy:
                raise
            return {
                "status": "error",
                "agent_runtime": "graph_shadow",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        return {
            "status": "completed",
            "agent_runtime": "graph_shadow",
            "graph_run_id": shadow_response.get("graph_run_id"),
            "graph_trace": dict(shadow_response.get("graph_trace", {}) or {}),
            "graph_events": list(shadow_response.get("graph_events", []) or []),
            "turn_decision": dict(shadow_response.get("turn_decision", {}) or {}),
            "prompt_trace": dict(shadow_response.get("prompt_trace", {}) or {}),
        }

    def _run_material_change_graph_shadow(
        self,
        record,
        *,
        reason: str,
    ) -> dict | None:
        if settings.agent_runtime != "graph_shadow":
            return None
        if not settings.agent_runtime_trace_enabled:
            return None
        try:
            shadow_response = self.graph_runtime.run_material_change(
                record,
                reason=self._graph_material_change_reason(reason),
            )
        except Exception as exc:
            if not settings.agent_runtime_fail_open_to_legacy:
                raise
            return {
                "status": "error",
                "agent_runtime": "graph_shadow",
                "error_type": exc.__class__.__name__,
                "error_message": str(exc),
            }
        return {
            "status": "completed",
            "agent_runtime": "graph_shadow",
            "graph_run_id": shadow_response.get("graph_run_id"),
            "graph_trace": dict(shadow_response.get("graph_trace", {}) or {}),
            "graph_events": list(shadow_response.get("graph_events", []) or []),
            "turn_decision": dict(shadow_response.get("turn_decision", {}) or {}),
            "prompt_trace": dict(shadow_response.get("prompt_trace", {}) or {}),
        }

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

    def _attach_graph_shadow(
        self,
        response: dict,
        graph_shadow: dict | None,
    ) -> None:
        if graph_shadow:
            response["graph_shadow"] = graph_shadow

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
            "owner": "graph_runtime",
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
        remaining_required_documents = list(
            response.get("remaining_required_documents", []) or requested_documents
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
                "graph_shadow": response.get("graph_shadow"),
                "agent_runtime": response.get("agent_runtime"),
                "selected_public_runtime": response.get("selected_public_runtime"),
                "graph_run_id": response.get("graph_run_id"),
                "graph_trace": response.get("graph_trace"),
                "graph_events": response.get("graph_events"),
                "graph_runtime_error": response.get("graph_runtime_error"),
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
            }
        )
        if response.get("agent_runtime") == "graph":
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
        response["requested_documents"] = list(
            response.get("requested_documents", [])
            or runtime_view_state.get("requested_documents", [])
            or []
        )
        response["remaining_required_documents"] = list(
            response.get("remaining_required_documents", [])
            or runtime_view_state.get("remaining_required_documents", [])
            or []
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
            "graph_run_id": response.get("graph_run_id"),
            "graph_trace": dict(response.get("graph_trace", {}) or {}),
            "graph_events": list(response.get("graph_events", []) or []),
            "graph_shadow": response.get("graph_shadow"),
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
        requested_documents = list(
            response.get("requested_documents", [])
            or interviewer_state.get("requested_documents", [])
            or []
        )
        remaining_required_documents = list(
            response.get("remaining_required_documents", [])
            or interviewer_state.get("remaining_required_documents", [])
            or []
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
