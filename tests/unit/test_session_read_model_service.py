from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.session_read_model_service import SessionReadModelService


def test_session_read_model_service_builds_runtime_ledger_and_view_state(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-read-model.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "ready_for_interview"},
        )
        session_record.phase_state = "interview"
        session_record.current_governor_decision = "continue_interview"
        session_record.runtime_trace_json = [
            {
                "node_name": "turn_decision",
                "prompt_pack_id": "ds160.interviewer",
                "prompt_version": "v2",
                "provider": "openai",
                "model": "gpt-5.4",
                "metadata": {"reasoning_effort": "high"},
                "turn_decision": "continue_interview",
            }
        ]
        session_record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "category_fit": 78,
                "document_readiness": 82,
                "narrative_consistency": 75,
                "confidence": 80,
                "missing_evidence": [],
                "risk_flags": [],
                "summary": "missing=0 risk_flags=0",
            }
        ]
        session_record.governor_history_json = [
            {
                "decision": "continue_interview",
                "summary": "decision=continue_interview",
            }
        ]
        db.add(session_record)
        db.flush()

        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_record.session_id,
            content="I want to study computer science.",
            source="user_message",
            commit=False,
        )
        assistant_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="What is the purpose of your travel?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {
                        "kind": "interview_question",
                        "question": "What is the purpose of your travel?",
                    },
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
            commit=False,
        )
        db.add(session_record)
        db.commit()

        payload = SessionReadModelService(db).build(session_record.session_id)
        session_id = session_record.session_id
        assistant_turn_id = assistant_turn.turn_id

    assert payload.session_id == session_id
    assert payload.phase_state == "interview"
    assert payload.runtime_ledger.session_id == session_id
    assert payload.runtime_view_state.source_turn_id == assistant_turn_id
    assert payload.runtime_view_state.decision == "continue_interview"
    assert payload.runtime_view_state.current_key_question == (
        "What is the purpose of your travel?"
    )
    assert payload.runtime_view_state.prompt_trace == {
        "prompt_pack_id": "ds160.interviewer",
        "prompt_version": "v2",
        "provider": "openai",
        "model": "gpt-5.4",
        "reasoning_effort": "high",
    }


def test_session_read_model_service_raises_for_missing_session(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'session-read-model-missing.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        service = SessionReadModelService(db)
        try:
            service.build("sess-missing")
        except LookupError as exc:
            assert str(exc) == "Session not found: sess-missing"
        else:
            raise AssertionError("expected LookupError for missing session")
