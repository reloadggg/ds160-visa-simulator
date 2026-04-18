from sqlalchemy.orm import Session

from app.agents.model_factory import AgentModelFactory
from app.agents.question_agent import QuestionAgentRunner
from app.domain.contracts import ApplicantProfile
from app.repositories.session_repo import SessionRepository
from app.services.gate_runtime_service import GateRuntimeService
from app.services.interview_runtime_service import InterviewRuntimeService


class SessionNotFoundError(LookupError):
    def __init__(self, session_id: str) -> None:
        self.session_id = session_id
        super().__init__(f"Session not found: {session_id}")


class MessageService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.session_repo = SessionRepository(db)
        self.gate_runtime = GateRuntimeService(db)
        self.interview_runtime = InterviewRuntimeService(db)

    def handle_user_turn(self, session_id: str, message_text: str) -> dict:
        record = self.session_repo.get(session_id)
        if record is None:
            raise SessionNotFoundError(session_id)
        record = self.gate_runtime.refresh_session(session_id)
        if record.gate_status_json.get("status") != "ready_for_interview":
            return self.gate_runtime.build_gate_response(record)
        return self.interview_runtime.run_turn(record, message_text)
