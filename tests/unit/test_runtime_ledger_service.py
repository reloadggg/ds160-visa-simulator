from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.domain.runtime import ScoreHistoryEntry
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.services.runtime_ledger_service import RuntimeLedgerService


def test_runtime_ledger_service_projects_turn_aligned_events(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime-ledger.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        service = RuntimeLedgerService(db)
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "ready_for_interview"},
        )
        session_record.runtime_trace_json = [
            {"node_name": "receive_input", "summary": "input received"},
            {
                "node_name": "resolve_capability",
                "tool_calls": [{"name": "lookup_profile"}],
                "provider": "openai",
                "model": "gpt-5.4",
            },
            {
                "node_name": "turn_decision",
                "turn_decision": "continue_interview",
                "tool_calls": [],
            },
            {"node_name": "plan_followup", "summary": "next turn planned"},
            {
                "node_name": "turn_decision",
                "turn_decision": "need_more_evidence",
                "tool_calls": [],
            },
        ]
        session_record.score_history_json = [
            ScoreHistoryEntry(
                scoring_stage="interview_turn",
                category_fit=72,
                document_readiness=82,
                narrative_consistency=70,
                confidence=75,
                missing_evidence=[],
                risk_flags=[],
                summary="missing=0 risk_flags=0",
            ).model_dump(mode="json"),
            ScoreHistoryEntry(
                scoring_stage="interview_turn",
                category_fit=74,
                document_readiness=40,
                narrative_consistency=68,
                confidence=63,
                missing_evidence=["funding_proof"],
                risk_flags=[],
                summary="missing=1 risk_flags=0",
            ).model_dump(mode="json"),
        ]
        session_record.governor_history_json = [
            {"decision": "continue_interview", "summary": "decision=continue_interview"},
            {"decision": "need_more_evidence", "summary": "decision=need_more_evidence"},
        ]
        db.add(session_record)
        db.flush()

        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_record.session_id,
            content="I want to study in the U.S.",
            source="user_message",
        )
        first_assistant_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="What school will you attend?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "continue_interview",
                    "requested_documents": [],
                    "focus": {
                        "kind": "interview_question",
                        "question": "What school will you attend?",
                    },
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
        )
        repo.append_user_turn(
            session_id=session_record.session_id,
            content="I will attend a language program.",
            source="user_message",
        )
        second_assistant_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="Please upload your funding proof.",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "decision": "need_more_evidence",
                    "requested_documents": ["funding_proof"],
                    "focus": {
                        "kind": "required_document",
                        "document_type": "funding_proof",
                    },
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": ["funding_proof"],
                        "risk_level": "medium",
                    },
                }
            },
        )

        ledger = service.build_session_ledger(session_record.session_id)
        first_assistant_turn_id = first_assistant_turn.turn_id
        second_assistant_turn_id = second_assistant_turn.turn_id
        latest_view_state = service.latest_view_state(ledger)

    assert [turn.role for turn in ledger.turns] == ["user", "assistant", "user", "assistant"]
    assert ledger.turns[1].turn_record == {
        "decision": "continue_interview",
        "requested_documents": [],
        "focus": {
            "kind": "interview_question",
            "question": "What school will you attend?",
        },
        "advisory_summary": {
            "risk_codes": [],
            "missing_evidence": [],
            "risk_level": "none",
        },
    }
    assert [event["event_type"] for event in service.events_for_turn(ledger, first_assistant_turn_id)] == [
        "trace",
        "trace",
        "capability",
        "trace",
        "scorer",
        "boundary",
        "advisory",
    ]
    assert [event["event_type"] for event in service.events_for_turn(ledger, second_assistant_turn_id)] == [
        "trace",
        "trace",
        "scorer",
        "boundary",
        "advisory",
    ]
    assert len(service.trace_payloads(ledger)) == 5
    assert len(service.scorer_payloads(ledger)) == 2
    assert len(service.boundary_payloads(ledger)) == 2
    assert latest_view_state.source_turn_id == second_assistant_turn_id
    assert latest_view_state.decision == "need_more_evidence"
    assert latest_view_state.governor_decision == "need_more_evidence"
    assert latest_view_state.public_status == "waiting_key_proof"
    assert latest_view_state.risk_level == "medium"
    assert latest_view_state.current_focus == {
        "kind": "required_document",
        "document_type": "funding_proof",
    }
    assert latest_view_state.current_key_question is None
    assert latest_view_state.current_key_proof == "funding_proof"
    assert latest_view_state.current_risk_code is None
    assert latest_view_state.requested_documents == ["funding_proof"]
    assert latest_view_state.allowed_next_actions == [
        "upload_key_proof",
        "explain_missing_proof",
    ]
    assert latest_view_state.advisory_context == {
        "risk_codes": [],
        "missing_evidence": ["funding_proof"],
        "risk_level": "medium",
    }
    assert latest_view_state.prompt_trace == {}


def test_runtime_ledger_service_marks_extra_batches_as_session_orphans(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runtime-ledger-orphan.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        service = RuntimeLedgerService(db)
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "ready_for_interview"},
        )
        session_record.runtime_trace_json = [
            {"node_name": "receive_input", "summary": "first batch"},
            {"node_name": "turn_decision", "turn_decision": "continue_interview"},
            {"node_name": "receive_input", "summary": "second batch"},
            {"node_name": "turn_decision", "turn_decision": "need_more_evidence"},
        ]
        session_record.score_history_json = [
            {
                "scoring_stage": "interview_turn",
                "missing_evidence": [],
                "risk_flags": [],
                "category_fit": 70,
                "document_readiness": 80,
                "narrative_consistency": 70,
                "confidence": 70,
                "summary": "missing=0 risk_flags=0",
            },
            {
                "scoring_stage": "interview_turn",
                "missing_evidence": ["funding_proof"],
                "risk_flags": [],
                "category_fit": 70,
                "document_readiness": 30,
                "narrative_consistency": 70,
                "confidence": 70,
                "summary": "missing=1 risk_flags=0",
            },
        ]
        session_record.governor_history_json = [
            {"decision": "continue_interview"},
            {"decision": "need_more_evidence"},
        ]
        db.add(session_record)
        db.flush()

        repo = SessionTurnRepository(db)
        repo.append_user_turn(
            session_id=session_record.session_id,
            content="I want to study in the U.S.",
            source="user_message",
        )
        repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="What will you study?",
            source="interviewer_runtime_service",
            metadata_json={"turn_record": {"decision": "continue_interview"}},
        )

        ledger = service.build_session_ledger(session_record.session_id)

    orphan_events = [event for event in ledger.events if event.turn_id is None]

    assert orphan_events
    assert all(event.event_id.startswith("session-orphan:") for event in orphan_events)
    assert [event.event_type.value for event in orphan_events] == [
        "trace",
        "trace",
        "scorer",
        "boundary",
    ]
