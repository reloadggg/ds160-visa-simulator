from collections.abc import Generator
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord, SessionTurnRecord
from app.domain.runtime import build_initial_gate_status
from app.services.ai_material_bundle_generator_service import (
    AIMaterialBundleGeneratorService,
    GeneratedMaterialBundleOutput,
)


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
