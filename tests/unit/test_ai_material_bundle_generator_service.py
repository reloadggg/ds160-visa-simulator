from collections.abc import Generator
import json

import pytest
from agents import AgentOutputSchema
from agents.exceptions import UserError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.domain.runtime import build_initial_gate_status
from app.services.ai_material_bundle_generator_service import (
    AIMaterialBundleGeneratorService,
    GeneratedMaterialBundleOutput,
    OpenAIAgentsMaterialBundleRunner,
)
from app.services.runtime_errors import ProviderAPIError


class StubRunner:
    def __init__(self) -> None:
        self.prompts: list[str] = []

    def run(self, **kwargs):
        self.prompts.append(kwargs["prompt"])
        return GeneratedMaterialBundleOutput(
            documents=[
                {
                    "document_type": "ds160",
                    "filename": "ai_ds160.txt",
                    "raw_text": (
                        "Online Nonimmigrant Visa Application\n"
                        "Applicant: TEST APPLICANT\n"
                        "Purpose: STUDENT (F1)\n"
                    ),
                    "fields": {
                        "/identity/full_name": "TEST APPLICANT",
                        "/visa_intent/travel_purpose": "STUDENT (F1)",
                    },
                },
                {
                    "document_type": "passport_bio",
                    "filename": "ai_passport.txt",
                    "raw_text": (
                        "PASSPORT BIOGRAPHIC PAGE\n"
                        "Full Name: TEST APPLICANT\n"
                        "Passport No.: X12345678\n"
                    ),
                    "fields": {
                        "/identity/full_name": "TEST APPLICANT",
                        "/identity/passport_number": "X12345678",
                    },
                },
                {
                    "document_type": "i20",
                    "filename": "ai_i20.txt",
                    "raw_text": (
                        "Certificate of Eligibility for Nonimmigrant Student Status\n"
                        "School Name: New York University\n"
                        "Program of Study: MS Computer Science\n"
                    ),
                    "fields": {
                        "/education/school_name": "New York University",
                        "/education/program_name": "MS Computer Science",
                    },
                },
                {
                    "document_type": "admission_letter",
                    "filename": "ai_admission.txt",
                    "raw_text": (
                        "New York University\n"
                        "Office of Graduate Admission\n"
                        "Program: MS Computer Science\n"
                    ),
                    "fields": {
                        "/education/school_name": "New York University",
                        "/education/program_name": "MS Computer Science",
                    },
                },
                {
                    "document_type": "funding_proof",
                    "filename": "ai_funding.txt",
                    "raw_text": (
                        "Bank Balance Certificate\n"
                        "Primary Source of Support: parents\n"
                        "Available Balance: USD 90000\n"
                    ),
                    "fields": {
                        "/funding/primary_source": "parents",
                        "/funding/available_funds": "90000",
                    },
                },
            ],
            synthetic_turns=[
                {
                    "role": "user",
                    "content": "I will study MS Computer Science at New York University.",
                    "field_claims": {
                        "/education/school_name": "New York University",
                    },
                }
            ],
        )


class StubFactory:
    def build_runtime_config(self, module_key: str, stage_key: str, declared_family=None):
        assert module_key == "material_generator_agent"
        assert stage_key == "interview_turn"
        return {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        }


@pytest.fixture()
def db_session(tmp_path) -> Generator[Session, None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'ai-material-generator.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    db = testing_session_local()
    try:
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def seed_session(db: Session) -> SessionRecord:
    record = SessionRecord(
        session_id="sess-ai-material",
        declared_family="f1",
        gate_status_json=build_initial_gate_status(
            declared_family="f1",
            required_documents=["ds160", "passport_bio", "i20", "admission_letter"],
        ),
        profile_json={},
        runtime_trace_json=[],
        score_history_json=[],
        governor_history_json=[],
        interviewer_state_json={},
        current_focus_json={},
    )
    db.add(record)
    db.add(
        SessionTurnRecord(
            turn_id="turn-seed",
            session_id=record.session_id,
            role="user",
            content="我会去 New York University 读 MS Computer Science，父母资助。",
            source="user",
            turn_index=1,
            metadata_json={},
        )
    )
    db.commit()
    db.refresh(record)
    return record


def test_ai_material_generator_includes_seed_and_transcript_in_prompt(
    db_session: Session,
) -> None:
    runner = StubRunner()
    record = seed_session(db_session)

    output, trace = AIMaterialBundleGeneratorService(
        db_session,
        model_factory=StubFactory(),
        runner=runner,
    ).generate(
        record=record,
        scenario="normal_f1_bundle",
        seed_text="我要去 New York University 读 MS Computer Science，父母资助。",
        include_synthetic_user_turns=True,
    )

    assert output.documents[2].fields["/education/school_name"] == "New York University"
    assert trace["generator"] == "openai_agents_sdk"
    prompt = json.loads(runner.prompts[0])
    assert prompt["seed_text"] == "我要去 New York University 读 MS Computer Science，父母资助。"
    assert prompt["transcript"][0]["content"] == (
        "我会去 New York University 读 MS Computer Science，父母资助。"
    )


def test_openai_agents_runner_uses_non_strict_output_schema(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeAgent:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class FakeAsyncOpenAI:
        def __init__(self, *, api_key: str, base_url: str, timeout: float) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["timeout"] = timeout

    class FakeResult:
        def final_output_as(self, output_type, raise_if_incorrect_type: bool = True):
            captured["final_output_type"] = output_type
            captured["raise_if_incorrect_type"] = raise_if_incorrect_type
            return GeneratedMaterialBundleOutput(
                documents=[
                    {
                        "document_type": "ds160",
                        "filename": "ai_ds160.txt",
                        "raw_text": (
                            "Online Nonimmigrant Visa Application\n"
                            "Applicant: TEST APPLICANT\n"
                        ),
                        "fields": {"/identity/full_name": "TEST APPLICANT"},
                    },
                    {
                        "document_type": "passport_bio",
                        "filename": "ai_passport.txt",
                        "raw_text": (
                            "PASSPORT BIOGRAPHIC PAGE\n"
                            "Full Name: TEST APPLICANT\n"
                        ),
                        "fields": {"/identity/full_name": "TEST APPLICANT"},
                    },
                    {
                        "document_type": "i20",
                        "filename": "ai_i20.txt",
                        "raw_text": "Certificate of Eligibility\nSchool Name: New York University\n",
                        "fields": {"/education/school_name": "New York University"},
                    },
                    {
                        "document_type": "admission_letter",
                        "filename": "ai_admission.txt",
                        "raw_text": "New York University\nOffice of Graduate Admission\n",
                        "fields": {"/education/school_name": "New York University"},
                    },
                    {
                        "document_type": "funding_proof",
                        "filename": "ai_funding.txt",
                        "raw_text": (
                            "Bank Balance Certificate\n"
                            "Available Balance: USD 90000\n"
                        ),
                        "fields": {"/funding/available_funds": "90000"},
                    },
                ],
            )

    def fake_run_sync(agent, prompt, *, max_turns: int, run_config):
        captured["agent"] = agent
        captured["prompt"] = prompt
        captured["max_turns"] = max_turns
        captured["workflow_name"] = run_config.workflow_name
        return FakeResult()

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        settings_module.settings,
        "ai_material_bundle_timeout_seconds",
        240.0,
    )
    monkeypatch.setattr(
        "app.services.ai_material_bundle_generator_service.Agent",
        FakeAgent,
    )
    monkeypatch.setattr(
        "app.services.ai_material_bundle_generator_service.AsyncOpenAI",
        FakeAsyncOpenAI,
    )
    monkeypatch.setattr(
        "app.services.ai_material_bundle_generator_service.Runner.run_sync",
        fake_run_sync,
    )

    output = OpenAIAgentsMaterialBundleRunner().run(
        prompt="{}",
        instructions="generate materials",
        output_type=GeneratedMaterialBundleOutput,
        runtime={"provider": "openai_compatible", "model": "gpt-5.4"},
    )

    output_schema = captured["output_type"]
    assert isinstance(output_schema, AgentOutputSchema)
    assert output_schema.is_strict_json_schema() is False
    assert captured["timeout"] == 240.0
    assert captured["final_output_type"] is GeneratedMaterialBundleOutput
    assert output.documents[2].fields["/education/school_name"] == "New York University"


def test_ai_material_generator_agent_errors_include_detail(
    db_session: Session,
) -> None:
    class FailingRunner:
        def run(self, **kwargs):
            raise UserError("Strict JSON schema is enabled")

    record = seed_session(db_session)

    with pytest.raises(ProviderAPIError) as exc_info:
        AIMaterialBundleGeneratorService(
            db_session,
            model_factory=StubFactory(),
            runner=FailingRunner(),
        ).generate(
            record=record,
            scenario="normal_f1_bundle",
            seed_text="我要去 New York University 读 MS Computer Science，父母资助。",
            include_synthetic_user_turns=True,
        )

    assert "UserError: Strict JSON schema is enabled" in str(exc_info.value)
