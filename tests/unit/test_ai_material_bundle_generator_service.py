from collections.abc import Generator
import json
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.domain.runtime import build_initial_gate_status
from app.services.ai_material_bundle_generator_service import (
    AIMaterialBundleGeneratorService,
    GeneratedMaterialDocument,
    GeneratedMaterialBundleOutput,
    OpenAIChatCompletionsMaterialBundleRunner,
    find_oracle_text_marker,
)
from app.services.runtime_errors import ModelRuntimeError


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
                {
                    "document_type": "relationship_proof_between_applicant_and_sponsors",
                    "filename": "ai_relationship.txt",
                    "raw_text": (
                        "Household Register Extract\n"
                        "Applicant: TEST APPLICANT\n"
                        "Relationship to sponsors: child\n"
                    ),
                    "fields": {
                        "/identity/full_name": "TEST APPLICANT",
                        "/funding/sponsor_relationship": "parents",
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


def test_ai_material_generator_uses_explicit_seed_without_transcript(
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
    assert trace["generator"] == "openai_chat_completions"
    prompt = json.loads(runner.prompts[0])
    assert prompt["seed_text"] == "我要去 New York University 读 MS Computer Science，父母资助。"
    assert "transcript" not in prompt
    assert prompt["required_documents"] == [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
        "relationship_proof_between_applicant_and_sponsors",
    ]


def test_ai_material_generator_uses_j1_template_documents(
    db_session: Session,
) -> None:
    class J1Runner(StubRunner):
        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return GeneratedMaterialBundleOutput(
                documents=[
                    {
                        "document_type": "ds160",
                        "filename": "j1_ds160.txt",
                        "raw_text": "DS-160\nPurpose: EXCHANGE VISITOR (J1)\n",
                        "fields": {"/visa_intent/travel_purpose": "J1"},
                    },
                    {
                        "document_type": "passport_bio",
                        "filename": "j1_passport.txt",
                        "raw_text": "Passport\nFull Name: Morgan Lee\n",
                        "fields": {"/identity/full_name": "Morgan Lee"},
                    },
                    {
                        "document_type": "ds2019",
                        "filename": "j1_ds2019.txt",
                        "raw_text": "Form DS-2019\nProgram Sponsor: Example Exchange\n",
                        "fields": {"/exchange/program_sponsor": "Example Exchange"},
                    },
                    {
                        "document_type": "funding_proof",
                        "filename": "j1_funding.txt",
                        "raw_text": "Funding Letter\nSupport Amount: USD 42000\n",
                        "fields": {"/funding/available_funds": "42000"},
                    },
                    {
                        "document_type": "program_invitation",
                        "filename": "j1_invitation.txt",
                        "raw_text": "Exchange Program Invitation\nHost: Example Lab\n",
                        "fields": {"/exchange/host": "Example Lab"},
                    },
                    {
                        "document_type": "sevis_fee_receipt",
                        "filename": "j1_sevis.txt",
                        "raw_text": "SEVIS I-901 Fee Receipt\nStatus: Paid\n",
                        "fields": {"/sevis/status": "paid"},
                    },
                ],
                synthetic_turns=[],
            )

    runner = J1Runner()
    record = seed_session(db_session)
    record.declared_family = "j1"

    output, trace = AIMaterialBundleGeneratorService(
        db_session,
        model_factory=StubFactory(),
        runner=runner,
    ).generate(
        record=record,
        scenario="normal_j1_bundle",
        seed_text="我要去 Example Lab 做 J-1 exchange visitor，项目资助。",
        include_synthetic_user_turns=False,
    )

    prompt = json.loads(runner.prompts[0])
    assert prompt["target_family"] == "j1"
    assert prompt["required_documents"] == [
        "ds160",
        "passport_bio",
        "ds2019",
        "funding_proof",
        "program_invitation",
        "sevis_fee_receipt",
    ]
    assert prompt["family_material_guidance"]["avoid_documents"] == [
        "i20",
        "admission_letter",
    ]
    assert trace["target_family"] == "j1"
    assert {document.document_type for document in output.documents} >= {
        "ds2019",
        "program_invitation",
        "sevis_fee_receipt",
    }


def test_ai_material_generator_rejects_family_template_missing_required_document(
    db_session: Session,
) -> None:
    class IncompleteH1BRunner(StubRunner):
        def run(self, **kwargs):
            self.prompts.append(kwargs["prompt"])
            return GeneratedMaterialBundleOutput(
                documents=[
                    {
                        "document_type": "ds160",
                        "filename": "h1b_ds160.txt",
                        "raw_text": "DS-160\nPurpose: H1B\n",
                        "fields": {"/visa_intent/travel_purpose": "H1B"},
                    },
                    {
                        "document_type": "passport_bio",
                        "filename": "h1b_passport.txt",
                        "raw_text": "Passport\nFull Name: Morgan Lee\n",
                        "fields": {"/identity/full_name": "Morgan Lee"},
                    },
                    {
                        "document_type": "i797",
                        "filename": "h1b_i797.txt",
                        "raw_text": "I-797 Approval Notice\nEmployer: Example Inc\n",
                        "fields": {"/employment/employer_name": "Example Inc"},
                    },
                    {
                        "document_type": "employer_letter",
                        "filename": "h1b_employer.txt",
                        "raw_text": "Employer Support Letter\nRole: Software Engineer\n",
                        "fields": {"/employment/role": "Software Engineer"},
                    },
                    {
                        "document_type": "lca",
                        "filename": "h1b_lca.txt",
                        "raw_text": "Labor Condition Application\nSOC: Software Developers\n",
                        "fields": {"/employment/soc": "Software Developers"},
                    },
                ],
                synthetic_turns=[],
            )

    record = seed_session(db_session)
    record.declared_family = "h1b"

    with pytest.raises(ModelRuntimeError) as exc_info:
        AIMaterialBundleGeneratorService(
            db_session,
            model_factory=StubFactory(),
            runner=IncompleteH1BRunner(),
        ).generate(
            record=record,
            scenario="normal_h1b_bundle",
            seed_text="我会去 Example Inc 做 H-1B 软件工程师。",
            include_synthetic_user_turns=False,
        )

    assert "degree_certificate" in exc_info.value.detail
    assert exc_info.value.status_code == 502
    assert exc_info.value.error_category == "model_output_invalid"
    assert exc_info.value.upstream_code == "model_output_invalid"
    assert exc_info.value.provider == "openai_compatible"
    assert exc_info.value.model == "gpt-5.4"


def test_openai_chat_runner_uses_json_object_response_format(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeCompletions:
        def create(self, **kwargs):
            captured["completion_kwargs"] = kwargs
            content = json.dumps(
                {
                    "documents": [
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
                            "raw_text": (
                                "Certificate of Eligibility\n"
                                "School Name: New York University\n"
                            ),
                            "fields": {
                                "/education/school_name": "New York University"
                            },
                        },
                        {
                            "document_type": "admission_letter",
                            "filename": "ai_admission.txt",
                            "raw_text": (
                                "New York University\n"
                                "Office of Graduate Admission\n"
                            ),
                            "fields": {
                                "/education/school_name": "New York University"
                            },
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
                    "synthetic_turns": [],
                    "generation_notes": [],
                }
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeOpenAI:
        def __init__(
            self,
            *,
            api_key: str,
            base_url: str,
            timeout: float,
            default_headers: dict[str, str],
        ) -> None:
            captured["api_key"] = api_key
            captured["base_url"] = base_url
            captured["timeout"] = timeout
            captured["default_headers"] = default_headers
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        settings_module.settings,
        "ai_material_bundle_timeout_seconds",
        240.0,
    )
    monkeypatch.setattr(
        "app.services.ai_material_bundle_generator_service.OpenAI",
        FakeOpenAI,
    )

    output = OpenAIChatCompletionsMaterialBundleRunner().run(
        prompt="{}",
        instructions="generate materials",
        output_type=GeneratedMaterialBundleOutput,
        runtime={"provider": "openai_compatible", "model": "gpt-5.4"},
    )

    completion_kwargs = captured["completion_kwargs"]
    assert completion_kwargs["model"] == "gpt-5.4"
    assert completion_kwargs["response_format"] == {"type": "json_object"}
    assert completion_kwargs["messages"][0] == {
        "role": "system",
        "content": "generate materials",
    }
    assert captured["timeout"] == 240.0
    assert captured["default_headers"] == {"User-Agent": "curl/8.5.0"}
    assert output.documents[2].fields["/education/school_name"] == "New York University"


def test_openai_chat_runner_normalizes_common_model_aliases(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeCompletions:
        def create(self, **kwargs):
            del kwargs
            content = json.dumps(
                {
                    "materials": [
                        {
                            "document_type": "ds160",
                            "body": "DS-160\nApplicant: TEST APPLICANT\n",
                            "fields": {"/identity/full_name": "TEST APPLICANT"},
                        },
                        {
                            "document_type": "passport_bio",
                            "sections": [
                                {
                                    "heading": "Passport",
                                    "lines": ["Applicant: TEST APPLICANT"],
                                }
                            ],
                            "fields": {"/identity/full_name": "TEST APPLICANT"},
                        },
                        {
                            "document_type": "i20",
                            "plain_text": "I-20\nSchool Name: New York University\n",
                            "fields": {
                                "/education/school_name": "New York University"
                            },
                        },
                        {
                            "document_type": "admission_letter",
                            "plain_text": "Admission\nNew York University\n",
                            "fields": {
                                "/education/school_name": "New York University"
                            },
                        },
                        {
                            "document_type": "funding_proof",
                            "plain_text": "Bank\nAvailable Balance: USD 90000\n",
                            "fields": {"/funding/available_funds": "90000"},
                        },
                    ],
                    "synthetic_user_turns": ["I will study at New York University."],
                    "generation_notes": "normalized from provider output",
                }
            )
            return SimpleNamespace(
                choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
            )

    class FakeOpenAI:
        def __init__(self, **kwargs) -> None:
            del kwargs
            self.chat = SimpleNamespace(completions=FakeCompletions())

    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setattr(
        "app.services.ai_material_bundle_generator_service.OpenAI",
        FakeOpenAI,
    )

    output = OpenAIChatCompletionsMaterialBundleRunner().run(
        prompt="{}",
        instructions="generate materials",
        output_type=GeneratedMaterialBundleOutput,
        runtime={"provider": "openai_compatible", "model": "gpt-5.4"},
    )

    assert output.documents[0].filename == "ai_ds160.txt"
    assert output.documents[0].raw_text.startswith("DS-160")
    assert output.synthetic_turns[0].content == (
        "I will study at New York University."
    )
    assert output.generation_notes == ["normalized from provider output"]


def test_generated_material_allows_real_issue_date_and_stringifies_fields() -> None:
    document = GeneratedMaterialDocument.model_validate(
        {
            "document_type": "passport_bio",
            "filename": "passport.txt",
            "raw_text": "Passport\nDate of Issue: 01 JAN 2025\n",
            "fields": {"/funding/available_funds": 90000},
        }
    )

    assert find_oracle_text_marker(document.raw_text) is None
    assert document.fields["/funding/available_funds"] == "90000"


def test_generated_material_accepts_path_value_field_list() -> None:
    document = GeneratedMaterialDocument.model_validate(
        {
            "document_type": "funding_proof",
            "filename": "funding.txt",
            "raw_text": "Bank\nAvailable Balance: USD 90000\n",
            "fields": [
                {"path": "/funding/primary_source", "value": "parents"},
                {"field_path": "/funding/available_funds", "value": 90000},
                {"pointer": "/education/program_name", "value": "MS Computer Science"},
                {"json_pointer": "/education/school_name", "value": "NYU"},
            ],
        }
    )

    assert document.fields == {
        "/funding/primary_source": "parents",
        "/funding/available_funds": "90000",
        "/education/program_name": "MS Computer Science",
        "/education/school_name": "NYU",
    }


def test_generated_material_blocks_oracle_issue_line() -> None:
    assert find_oracle_text_marker("Issue: funding evidence is missing") == "Issue:"


def test_ai_material_generator_runner_errors_include_detail(
    db_session: Session,
) -> None:
    class FailingRunner:
        def run(self, **kwargs):
            raise RuntimeError("provider returned malformed JSON")

    record = seed_session(db_session)

    with pytest.raises(ModelRuntimeError) as exc_info:
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

    assert "RuntimeError: provider returned malformed JSON" in str(exc_info.value)
    assert exc_info.value.status_code == 503


def test_ai_material_generator_json_parse_errors_are_model_output_invalid(
    db_session: Session,
) -> None:
    class InvalidJsonRunner:
        def run(self, **kwargs):
            raise json.JSONDecodeError("Expecting value", "not-json", 0)

    record = seed_session(db_session)

    with pytest.raises(ModelRuntimeError) as exc_info:
        AIMaterialBundleGeneratorService(
            db_session,
            model_factory=StubFactory(),
            runner=InvalidJsonRunner(),
        ).generate(
            record=record,
            scenario="normal_f1_bundle",
            seed_text="我要去 New York University 读 MS Computer Science，父母资助。",
            include_synthetic_user_turns=True,
        )

    assert exc_info.value.status_code == 502
    assert exc_info.value.error_category == "model_output_invalid"
    assert exc_info.value.upstream_code == "model_output_invalid"
    assert exc_info.value.provider == "openai_compatible"
    assert exc_info.value.model == "gpt-5.4"
    assert "材料生成模型输出结构不合格" in exc_info.value.detail
