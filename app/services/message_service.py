from sqlalchemy.orm import Session

from app.db.models import SessionTurnRecord
from app.domain.runtime import GateOverallStatus
from app.platform.turn_record import TurnRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.gate_runtime_service import GateRuntimeService
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


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.session_turn_repo = SessionTurnRepository(db)
        self.gate_runtime = GateRuntimeService(db)
        self.interviewer_runtime = InterviewerRuntimeService(db)
        self.session_read_model = SessionReadModelService(db)

    def handle_user_turn(self, session_id: str, message_text: str) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        if self._is_refusal_closed(record):
            raise SessionClosedError(session_id, self._closed_session_detail(record))
        record = self.gate_runtime.refresh_session(session_id, save=False)

        try:
            self.session_turn_repo.append_user_turn(
                session_id=record.session_id,
                content=message_text,
                source="user_message",
                metadata_json={"phase_state": record.phase_state},
                commit=False,
            )

            if record.gate_status_json.get("status") != GateOverallStatus.READY_FOR_INTERVIEW:
                response = self.gate_runtime.build_gate_response(record)
                assistant_turn = self._append_assistant_turn(record, response)
                self._sync_runtime_view_contract(record, response, assistant_turn)
                self.session_repo.save(record)
                return response

            interview_response = self.interviewer_runtime.run_turn(record, message_text)
            response = self.gate_runtime.merge_interview_response(interview_response, record)
            assistant_turn = self._append_assistant_turn(record, response)
            self._sync_runtime_view_contract(record, response, assistant_turn)
            self.session_repo.save(record)
            return response
        except Exception:
            self.db.rollback()
            raise

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
            interview_response = self.interviewer_runtime.refresh_after_material_change(
                record,
                reason=reason,
            )
            response = self.gate_runtime.merge_interview_response(
                interview_response,
                record,
            )
            assistant_turn = self._append_assistant_turn(record, response)
            self._sync_runtime_view_contract(record, response, assistant_turn)
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
        source = (
            "gate_runtime_service"
            if record.gate_status_json.get("status") == GateOverallStatus.FAMILY_NOT_SELECTED
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
        if runtime_view_state.get("source_turn_id") == assistant_turn.turn_id:
            metadata["runtime_view_state"] = runtime_view_state
        assistant_turn.metadata_json = metadata

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
