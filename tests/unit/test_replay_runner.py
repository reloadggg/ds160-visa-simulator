from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.domain.runtime import ScoreHistoryEntry
from app.domain.case_memory import (
    CaseClaim,
    EvidenceCard,
    MaterialUnderstandingJob,
    MaterialUnderstandingResult,
)
from app.db.models import DocumentRecord
from app.repositories.session_repo import SessionRepository
from app.repositories.session_turn_repo import SessionTurnRepository
from app.evals.replay_runner import ReplayRunner
from app.services.case_memory_service import CaseMemoryService


def test_replay_runner_inspects_single_turn(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'replay-inspect.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "ready_for_interview"},
        )
        session_record.runtime_trace_json = [
            {
                "node_name": "decide_capability",
                "tool_calls": [{"name": "lookup_profile"}],
                "provider": "openai",
                "model": "gpt-5.4",
            },
            {
                "node_name": "turn_decision",
                "turn_decision": "continue_interview",
                "tool_calls": [],
            },
        ]
        session_record.score_history_json = [
            ScoreHistoryEntry(
                scoring_stage="interview_turn",
                category_fit=72,
                document_readiness=80,
                narrative_consistency=70,
                confidence=68,
                missing_evidence=[],
                risk_flags=[],
                summary="missing=0 risk_flags=0",
            ).model_dump(mode="json")
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
        user_turn = repo.append_user_turn(
            session_id=session_record.session_id,
            content="My parents will pay for my studies.",
            source="user_message",
        )
        assistant_turn = repo.append_assistant_turn(
            session_id=session_record.session_id,
            content="What is the purpose of your travel?",
            source="interviewer_runtime_service",
            metadata_json={
                "turn_record": {
                    "turn_id": "turn-assistant-1",
                    "session_id": session_record.session_id,
                    "user_turn_id": user_turn.turn_id,
                    "assistant_turn_id": "turn-assistant-1",
                    "user_input": "My parents will pay for my studies.",
                    "decision": "continue_interview",
                    "assistant_message": "What is the purpose of your travel?",
                    "requested_documents": [],
                    "focus": {"kind": "interview_question"},
                    "trace_refs": ["receive_input", "turn_decision"],
                    "artifacts": [],
                    "advisory_summary": {
                        "risk_codes": [],
                        "missing_evidence": [],
                        "risk_level": "none",
                    },
                }
            },
        )

        payload = ReplayRunner(db).inspect_turn(
            session_record.session_id,
            assistant_turn.turn_id,
        )
        user_turn_id = user_turn.turn_id
        assistant_turn_id = assistant_turn.turn_id

    assert payload["turn_id"] == assistant_turn_id
    assert payload["role"] == "assistant"
    assert payload["turn_record"]["user_turn_id"] == user_turn_id
    assert payload["turn_record"]["decision"] == "continue_interview"
    assert [event["event_type"] for event in payload["events"]] == [
        "trace",
        "capability",
        "trace",
        "scorer",
        "boundary",
        "advisory",
    ]


def test_replay_runner_replays_session_in_order(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'replay-session.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)

    with Session(engine) as db:
        session_record = SessionRepository(db).create(
            declared_family="f1",
            gate_status_json={"status": "ready_for_interview"},
        )
        session_record.score_history_json = [
            ScoreHistoryEntry(
                scoring_stage="interview_turn",
                category_fit=72,
                document_readiness=40,
                narrative_consistency=55,
                confidence=65,
                missing_evidence=["funding_proof"],
                risk_flags=[],
                summary="missing=1 risk_flags=0",
            ).model_dump(mode="json")
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
            content="What is the purpose of your travel?",
            source="interviewer_runtime_service",
            metadata_json={"turn_record": {"decision": "continue_interview"}},
        )
        document = DocumentRecord(
            document_id="doc-i20",
            session_id=session_record.session_id,
            filename="i20.pdf",
            artifact_json={"document_type": "i20"},
            raw_bytes=b"i20",
        )
        db.add(document)
        db.flush()
        CaseMemoryService(db).upsert_material_understanding(
            document_id="doc-i20",
            job=MaterialUnderstandingJob(
                job_id="job-i20",
                document_id="doc-i20",
                status="completed",
                result=MaterialUnderstandingResult(
                    evidence_cards=[
                        EvidenceCard(
                            evidence_id="ev-school",
                            source_type="uploaded_file",
                            document_id="doc-i20",
                            excerpt="School Name: Example University",
                            claim_refs=["claim-school"],
                            confidence=0.93,
                        )
                    ],
                    extracted_claims=[
                        CaseClaim(
                            claim_id="claim-school",
                            field_path="/education/school_name",
                            value="Example University",
                            status="documented",
                            supporting_evidence_ids=["ev-school"],
                            confidence=0.93,
                        )
                    ],
                    confidence=0.93,
                ),
            ),
        )

        payload = ReplayRunner(db).replay_session(session_record.session_id)

    assert payload["session_id"] == session_record.session_id
    assert payload["turn_count"] == 2
    assert payload["score_evals"] == [
        {
            "scoring_stage": "interview_turn",
            "risk_level": "medium",
            "risk_codes": [],
            "confirmed_high_risk_codes": [],
            "refusal_candidate_codes": [],
            "review_candidate_codes": [],
            "missing_evidence": ["funding_proof"],
            "missing_evidence_count": 1,
            "risk_flag_count": 0,
            "document_ready": False,
            "needs_more_evidence": True,
        }
    ]
    assert [turn["role"] for turn in payload["turns"]] == ["user", "assistant"]
    assert payload["turns"][1]["turn_record"] == {"decision": "continue_interview"}
    assert payload["case_memory"]["claims"][0]["field_path"] == (
        "/education/school_name"
    )
    assert payload["case_board"]["claims"][0]["value"] == "Example University"
    assert payload["turns"][0]["events"] == []
    assert [event["event_type"] for event in payload["turns"][1]["events"]] == [
        "scorer"
    ]
