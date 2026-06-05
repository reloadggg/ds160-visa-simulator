from __future__ import annotations

import json

import pytest
from agents.exceptions import AgentsException, ModelBehaviorError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.settings import settings
from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.services.native_interviewer_runtime_service import (
    NativeInterviewerOutput,
    NativeInterviewerRuntimeService,
    OpenAIAgentsInterviewerRunner,
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


def test_native_interviewer_output_accepts_visible_message_aliases() -> None:
    for key in (
        "response_text",
        "user_facing_message",
        "question",
        "interviewer_message",
        "text",
        "content",
    ):
        output = NativeInterviewerOutput.model_validate(
            {
                "schema_version": "native_interviewer.v0",
                key: "Which NYU courses support your return plan?",
            }
        )

        assert output.assistant_message == "Which NYU courses support your return plan?"


def test_native_interviewer_output_accepts_nested_output_content_alias() -> None:
    output = NativeInterviewerOutput.model_validate(
        {
            "schema_version": "native_interviewer.v0",
            "output": {
                "role": "visa_officer",
                "content": "What do your parents do in China to fund your studies?",
            },
        }
    )

    assert output.assistant_message == (
        "What do your parents do in China to fund your studies?"
    )


def test_native_interviewer_model_behavior_error_is_structured(
    db_session,
) -> None:
    service = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
    )

    error = service._normalize_model_error(
        ModelBehaviorError("invalid json"),
        runtime={"provider": "openai_compatible", "model": "gpt-5.4"},
    )

    assert error.status_code == 502
    assert error.error_category == "model_output_invalid"
    assert error.upstream_code == "model_output_invalid"
    assert "结构化输出" in error.detail
    assert error.to_public_payload() == {
        "status": 502,
        "detail": error.detail,
        "error_category": "model_output_invalid",
        "upstream_code": "model_output_invalid",
        "provider": "openai_compatible",
        "model": "gpt-5.4",
    }


def test_native_interviewer_model_behavior_error_with_cause_keeps_output_category(
    db_session,
) -> None:
    service = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
    )
    try:
        raise ModelBehaviorError("invalid json") from ValueError(
            "pydantic validation failed",
        )
    except ModelBehaviorError as exc:
        model_error = exc

    error = service._normalize_model_error(
        model_error,
        runtime={"provider": "openai_compatible", "model": "claude-sonnet-4-6"},
    )

    assert error.status_code == 502
    assert error.error_category == "model_output_invalid"
    assert error.upstream_code == "model_output_invalid"
    assert error.model == "claude-sonnet-4-6"


def test_openai_compatible_runner_uses_json_chat_without_agents(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["completion_kwargs"] = kwargs

            class FakeMessage:
                content = (
                    "```json\n"
                    '{"assistant_message":"Your materials contain a key mismatch.",'
                    '"decision":"high_risk_review"}\n'
                    "```"
                )

            class FakeChoice:
                message = FakeMessage()

            class FakeCompletion:
                choices = [FakeChoice()]

            return FakeCompletion()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = type(
                "FakeChat",
                (),
                {"completions": FakeCompletions()},
            )()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.Runner.run_sync",
        lambda *args, **kwargs: pytest.fail("Agents SDK should not run"),
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAI",
        FakeOpenAI,
    )

    output = OpenAIAgentsInterviewerRunner().run(
        prompt="{}",
        instructions="Return JSON.",
        output_type=NativeInterviewerOutput,
        runtime={"provider": "openai_compatible", "model": "claude-sonnet-4-6"},
    )

    assert output.assistant_message == "Your materials contain a key mismatch."
    assert output.decision == "high_risk_review"
    assert captured["client_kwargs"] == {
        "api_key": "test-key",
        "base_url": "https://example.test/v1",
        "timeout": settings.openai_timeout_seconds,
        "max_retries": 0,
        "default_headers": {"User-Agent": "curl/8.5.0"},
    }
    assert captured["completion_kwargs"]["model"] == "claude-sonnet-4-6"
    assert captured["completion_kwargs"]["response_format"] == {"type": "json_object"}


def test_openai_agents_runner_falls_back_to_json_chat_for_invalid_agent_output(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeRunResult:
        def final_output_as(self, output_type, *, raise_if_incorrect_type: bool):
            del output_type, raise_if_incorrect_type
            raise ModelBehaviorError("invalid json")

    class FakeCompletions:
        def create(self, **kwargs):
            captured["completion_kwargs"] = kwargs

            class FakeMessage:
                content = (
                    '{"assistant_message":"Fallback question?",'
                    '"decision":"continue_interview"}'
                )

            class FakeChoice:
                message = FakeMessage()

            class FakeCompletion:
                choices = [FakeChoice()]

            return FakeCompletion()

    class FakeOpenAI:
        def __init__(self, **kwargs):
            captured["client_kwargs"] = kwargs
            self.chat = type(
                "FakeChat",
                (),
                {"completions": FakeCompletions()},
            )()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.Runner.run_sync",
        lambda *args, **kwargs: FakeRunResult(),
    )
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAI",
        FakeOpenAI,
    )

    output = OpenAIAgentsInterviewerRunner().run(
        prompt="{}",
        instructions="Return JSON.",
        output_type=NativeInterviewerOutput,
        runtime={"provider": "openai", "model": "gpt-5.4"},
    )

    assert output.assistant_message == "Fallback question?"
    assert output.decision == "continue_interview"
    assert captured["completion_kwargs"]["model"] == "gpt-5.4"
    assert captured["completion_kwargs"]["response_format"] == {"type": "json_object"}


def test_native_interviewer_agent_error_is_structured(
    db_session,
) -> None:
    service = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
    )

    error = service._normalize_model_error(
        AgentsException("runner failed"),
        runtime={"provider": "openai_compatible", "model": "gpt-5.4"},
    )

    assert error.status_code == 503
    assert error.error_category == "agent_runtime_error"
    assert error.upstream_code == "agent_runtime_error"
    assert "模型代理运行失败" in error.detail


def test_native_interviewer_context_compacts_consecutive_assistant_questions(
    db_session,
) -> None:
    service = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
    )

    context = service._build_interviewer_context(
        {
            "full_transcript": [
                {
                    "turn_index": 1,
                    "role": "assistant",
                    "content": "Which engineering skill do you need most?",
                },
                {
                    "turn_index": 2,
                    "role": "assistant",
                    "content": "Did you apply right after undergraduate study?",
                },
                {
                    "turn_index": 3,
                    "role": "user",
                    "content": "I applied directly after undergraduate study.",
                },
            ]
        }
    )

    transcript = context["full_transcript"]
    assert transcript["policy"]["active_question"] == "latest_assistant_turn_only"
    assert transcript["turns"] == [
        {
            "turn_index": 2,
            "role": "assistant",
            "content": "Did you apply right after undergraduate study?",
        },
        {
            "turn_index": 3,
            "role": "user",
            "content": "I applied directly after undergraduate study.",
        },
    ]


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


def test_native_interviewer_advisory_missing_evidence_prefers_case_board(
    db_session,
) -> None:
    advisory = NativeInterviewerRuntimeService(
        db_session,
        model_factory=StubModelFactory(),
        agent_runner=QueueRunner([]),
    )._build_advisory_context(
        {
            "case_board": {
                "claims": [
                    {
                        "claim_id": "claim-funding",
                        "field_path": "/funding/primary_source",
                        "value": "parents",
                        "status": "documented",
                        "supporting_evidence_ids": ["ev-bank"],
                        "conflicting_evidence_ids": [],
                    }
                ],
                "evidence_cards": [
                    {
                        "evidence_id": "ev-bank",
                        "source_type": "uploaded_file",
                        "excerpt": "Parent sponsor account",
                        "claim_refs": ["claim-funding"],
                    }
                ],
                "proof_points": [
                    {
                        "proof_point_id": "proof-funding-chain",
                        "question": "Who pays for the first year?",
                        "status": "partial",
                    }
                ],
                "conflicts": [],
            },
            "score_history_tail": [
                {
                    "risk_flags": [],
                    "missing_evidence": ["legacy_funding_proof"],
                }
            ],
        }
    )

    assert advisory["missing_evidence"] == ["proof-funding-chain"]
