from app.platform.turn_record import TurnRecord


def test_turn_record_uses_latest_user_turn_until_assistant_turn_exists() -> None:
    record = TurnRecord.create(
        session_id="sess-1",
        user_turn_id="turn-user-1",
        user_input="hello",
        decision="continue_interview",
        assistant_message="question",
        requested_documents=[],
        focus={"kind": "interview_question"},
        trace_refs=["receive_input"],
        advisory_summary={
            "risk_codes": [],
            "missing_evidence": [],
            "risk_level": "none",
        },
    )

    assert record.turn_id == "turn-user-1"
    assert record.user_turn_id == "turn-user-1"
    assert record.assistant_turn_id is None


def test_turn_record_switches_to_assistant_turn_after_persist() -> None:
    record = TurnRecord.create(
        session_id="sess-1",
        user_turn_id="turn-user-1",
        user_input="hello",
        decision="continue_interview",
        assistant_message="question",
        requested_documents=[],
        focus={"kind": "interview_question"},
        trace_refs=["receive_input"],
        advisory_summary={
            "risk_codes": [],
            "missing_evidence": [],
            "risk_level": "none",
        },
    )

    finalized = record.with_assistant_turn("turn-assistant-2")

    assert finalized.turn_id == "turn-assistant-2"
    assert finalized.user_turn_id == "turn-user-1"
    assert finalized.assistant_turn_id == "turn-assistant-2"
