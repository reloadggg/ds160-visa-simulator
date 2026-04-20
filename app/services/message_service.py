from sqlalchemy.orm import Session

from app.domain.runtime import GateOverallStatus
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.services.interviewer_runtime_service import InterviewerRuntimeService


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

            if record.gate_status_json.get("status") == GateOverallStatus.FAMILY_NOT_SELECTED:
                response = self.gate_runtime.build_gate_response(record)
                self._append_assistant_turn(record, response)
                self.session_repo.save(record)
                return response

            interview_response = self.interviewer_runtime.run_turn(record, message_text)
            response = self.gate_runtime.merge_interview_response(interview_response, record)
            self._append_assistant_turn(record, response)
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
    ) -> None:
        source = (
            "gate_runtime_service"
            if record.gate_status_json.get("status") == GateOverallStatus.FAMILY_NOT_SELECTED
            else "interviewer_runtime_service"
        )
        self.session_turn_repo.append_assistant_turn(
            session_id=record.session_id,
            content=response["assistant_message"],
            source=source,
            metadata_json={
                "phase_state": record.phase_state,
                "governor_decision": response.get("governor_decision"),
                "current_focus_kind": (record.current_focus_json or {}).get("kind"),
            },
            commit=False,
        )
