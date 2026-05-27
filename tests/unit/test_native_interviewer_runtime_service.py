from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.services.native_interviewer_runtime_service import (
    NativeInterviewerOutput,
    NativeInterviewerRuntimeService,
)
from app.services.runtime_errors import ModelRuntimeError


class StubModelFactory:
    def __init__(self, model=object(), runtime: dict | None = None) -> None:
        self.model = model
        self.runtime = runtime or {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        }

    def build(self, *args, **kwargs):
        return self.model, dict(self.runtime)

    def build_runtime_config(self, *args, **kwargs):
        return dict(self.runtime)


class QueueRunner:
    def __init__(self, outputs: list[NativeInterviewerOutput]) -> None:
        self.outputs = list(outputs)
        self.requests: list[dict] = []

    def run(self, **request):
        self.requests.append(request)
        if not self.outputs:
            raise AssertionError("no queued output")
        return self.outputs.pop(0)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'native-interviewer.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    try:
        with Session(engine) as db:
            yield db
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_native_interviewer_retries_when_output_repeats_answered_fact(
    db_session,
) -> None:
    record = SessionRecord(
        session_id="sess-native-repeat",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
        gate_status_json={"status": "ready_for_interview"},
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-user-open",
            turn_index=1,
            session_id=record.session_id,
            role="user",
            content=(
                "你好，面试官。我准备前往纽约大学就读了，我读的专业是数据科学与数据技术。"
            ),
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-post",
            turn_index=2,
            session_id=record.session_id,
            role="assistant",
            content="毕业后你准备做什么工作？",
            source="native_interviewer_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-post",
            turn_index=3,
            session_id=record.session_id,
            role="user",
            content="我打算做程序开发类的工作吧。",
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-major",
            turn_index=4,
            session_id=record.session_id,
            role="assistant",
            content="你本科读的是什么专业？",
            source="native_interviewer_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-complaint",
            turn_index=5,
            session_id=record.session_id,
            role="user",
            content="我回答过你了，我要做程序类的工作",
            source="user_message",
            metadata_json={},
        ),
    ]
    db_session.add(record)
    db_session.add_all(turns)
    db_session.commit()

    runner = QueueRunner(
        [
            NativeInterviewerOutput(
                assistant_message="那你本科读的是什么专业？",
                decision="continue_interview",
            ),
            NativeInterviewerOutput(
                assistant_message=(
                    "你刚才已经说过毕业后想做程序开发。本科数据科学背景和这个项目怎样支持你的回国职业计划？"
                ),
                decision="continue_interview",
                memory_notes=[
                    "用户已说明本科数据科学与大数据技术",
                    "用户已说明毕业后做程序开发",
                ],
            ),
        ]
    )

    response = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
        agent_runner=runner,
    ).run_turn(record, "我回答过你了，我要做程序类的工作")

    assert len(runner.requests) == 2
    retry_prompt = json.loads(runner.requests[1]["prompt"])
    assert retry_prompt["validator_feedback"]
    assert "本科专业已经在上文回答过" in " ".join(
        retry_prompt["validator_feedback"]
    )
    assert "case_state" not in retry_prompt
    assert retry_prompt["interview_context"]["context_policy"][
        "legacy_extracted_hints_are_untrusted"
    ] is True
    assert response["assistant_message"] == (
        "你刚才已经说过毕业后想做程序开发。本科数据科学背景和这个项目怎样支持你的回国职业计划？"
    )
    assert response["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert response["prompt_trace"]["quality_attempt_count"] == 2
    assert response["selected_public_runtime"] == "native_interviewer"


def test_native_interviewer_blocks_bad_output_after_retry(db_session) -> None:
    record = SessionRecord(
        session_id="sess-native-block",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
        gate_status_json={"status": "ready_for_interview"},
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-user-open",
            turn_index=1,
            session_id=record.session_id,
            role="user",
            content="我本科读的是数据科学与大数据技术专业。",
            source="user_message",
            metadata_json={},
        )
    ]
    db_session.add(record)
    db_session.add_all(turns)
    db_session.commit()

    runner = QueueRunner(
        [
            NativeInterviewerOutput(
                assistant_message="你本科读的是什么专业？",
                decision="continue_interview",
            ),
            NativeInterviewerOutput(
                assistant_message="你本科读的是什么专业？",
                decision="continue_interview",
            ),
        ]
    )

    with pytest.raises(ModelRuntimeError):
        NativeInterviewerRuntimeService(
            db_session,
            model_factory=StubModelFactory(),
            agent_runner=runner,
        ).run_turn(record, "我回答过你了")


def test_native_interviewer_prompt_treats_legacy_extracts_as_untrusted_hints(
    db_session,
) -> None:
    record = SessionRecord(
        session_id="sess-native-hints",
        phase_state="interview",
        declared_family="f1",
        current_governor_decision="continue_interview",
        gate_status_json={
            "status": "ready_for_interview",
            "required_documents": [
                {"document_type": "school_admission", "status": "missing"}
            ],
        },
        profile_json={"education": {"program_name": None, "school_name": None}},
    )
    turns = [
        SessionTurnRecord(
            turn_id="turn-user-nyu",
            turn_index=1,
            session_id=record.session_id,
            role="user",
            content="我要去 NYU 读 data science，本科是数据科学与大数据技术。",
            source="user_message",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-assistant-post-study",
            turn_index=2,
            session_id=record.session_id,
            role="assistant",
            content="毕业后你打算回国做什么工作？",
            source="native_interviewer_runtime",
            metadata_json={},
        ),
        SessionTurnRecord(
            turn_id="turn-user-post-study",
            turn_index=3,
            session_id=record.session_id,
            role="user",
            content="毕业后我想做程序开发。",
            source="user_message",
            metadata_json={},
        ),
    ]
    db_session.add(record)
    db_session.add_all(turns)
    db_session.commit()

    runner = QueueRunner(
        [
            NativeInterviewerOutput(
                assistant_message=(
                    "你提到本科是数据科学与大数据技术，也计划在 NYU 读数据科学；"
                    "这个项目和你回国做程序开发之间具体怎么衔接？"
                ),
                decision="continue_interview",
            )
        ]
    )

    response = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
        agent_runner=runner,
    ).run_turn(record, "我刚才说了，毕业后做程序开发")

    prompt = json.loads(runner.requests[0]["prompt"])
    context = prompt["interview_context"]
    assert context["context_policy"]["source_of_truth_order"][0] == "full_transcript"
    assert context["context_policy"]["do_not_treat_missing_legacy_fields_as_unanswered"]
    assert context["legacy_extracted_hints"]["profile_json"]["education"] == {
        "program_name": None,
        "school_name": None,
    }
    assert any(
        "NYU" in turn["content"] and "data science" in turn["content"]
        for turn in context["full_transcript"]
    )
    assert response["assistant_message"].startswith("你提到本科是数据科学")
