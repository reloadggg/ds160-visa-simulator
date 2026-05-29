from collections.abc import Generator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.domain.runtime import build_initial_gate_status
from app.services.debug_material_bundle_service import (
    DEBUG_MATERIAL_BUNDLE_SCENARIOS,
    DebugMaterialBundleService,
)
from app.services.ai_material_bundle_generator_service import GeneratedMaterialBundleOutput
from app.services.runtime_errors import ModelRuntimeError
from app.repositories.session_turn_repo import SessionTurnRepository

ORACLE_TEXT_PHRASES = (
    "issue:",
    "missing:",
    "expected:",
    "defect:",
    "this conflicts with",
    "school mismatch",
    "identity mismatch",
    "funding shortfall",
    "claim vs document",
    "expected_findings",
)


def assert_no_oracle_text(value: str) -> None:
    normalized = value.casefold()
    assert not any(phrase in normalized for phrase in ORACLE_TEXT_PHRASES)


@pytest.fixture()
def db_session(tmp_path) -> Generator[Session, None, None]:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'debug-bundle.sqlite3'}",
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


def seed_session(db: Session, suffix: str = "default") -> str:
    session_id = f"sess-debug-bundle-{suffix}"
    db.add(
        SessionRecord(
            session_id=session_id,
            declared_family="f1",
            gate_status_json=build_initial_gate_status(
                declared_family="f1",
                scenario_key="parent_sponsored",
                required_documents=[
                    "ds160",
                    "passport_bio",
                    "i20",
                    "admission_letter",
                    "funding_proof",
                ],
            ),
            profile_json={},
            runtime_trace_json=[],
            score_history_json=[],
            governor_history_json=[],
            interviewer_state_json={},
            current_focus_json={},
        )
    )
    db.commit()
    return session_id


def test_all_debug_material_bundle_scenarios_create_visible_documents(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {
            "assistant_message": "继续说明你的学习计划。",
            "governor_decision": "continue_interview",
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "runtime_view_state": {},
        },
    )
    service = DebugMaterialBundleService(db_session)

    for index, scenario in enumerate(DEBUG_MATERIAL_BUNDLE_SCENARIOS):
        session_id = seed_session(db_session, str(index))
        payload = service.create_bundle(session_id, scenario=scenario)

        assert payload["scenario"] == scenario
        assert payload["documents"]
        assert len(payload["documents"]) >= 5
        assert payload["bundle_id"].startswith("dbg-bundle-")
        for document in payload["documents"]:
            assert document["raw_text"]
            assert document["fields"]
            assert document["content_url"].endswith(
                f"/files/{document['document_id']}/content"
            )
            assert_no_oracle_text(document["raw_text"])


def test_school_mismatch_bundle_uses_document_evidence_not_oracle_text(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="school_mismatch_bundle",
    )

    school_documents = {
        document["document_type"]: document["fields"].get("/education/school_name")
        for document in payload["documents"]
        if document["document_type"] in {"i20", "admission_letter"}
    }
    assert school_documents == {
        "i20": "Example University",
        "admission_letter": "Alternate Example University",
    }
    assert payload["expected_findings"][0]["visible_to_model"] is False

    persisted_documents = db_session.query(DocumentRecord).filter_by(
        session_id=session_id,
    ).all()
    persisted_chunks = db_session.query(DocumentChunkRecord).filter_by(
        session_id=session_id,
    ).all()
    persisted_evidence = db_session.query(EvidenceItemRecord).filter_by(
        session_id=session_id,
    ).all()

    forbidden_payloads = [
        *(document.raw_text for document in persisted_documents),
        *(chunk.text for chunk in persisted_chunks),
        *(item.excerpt for item in persisted_evidence),
    ]
    for value in forbidden_payloads:
        assert_no_oracle_text(value)


def test_all_debug_material_bundle_persisted_text_keeps_oracle_out(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    service = DebugMaterialBundleService(db_session)

    for index, scenario in enumerate(DEBUG_MATERIAL_BUNDLE_SCENARIOS):
        session_id = seed_session(db_session, f"oracle-{index}")
        payload = service.create_bundle(session_id, scenario=scenario)

        persisted_documents = db_session.query(DocumentRecord).filter_by(
            session_id=session_id,
        ).all()
        persisted_chunks = db_session.query(DocumentChunkRecord).filter_by(
            session_id=session_id,
        ).all()
        persisted_evidence = db_session.query(EvidenceItemRecord).filter_by(
            session_id=session_id,
        ).all()

        for document in payload["documents"]:
            assert_no_oracle_text(document["raw_text"])
        for value in [
            *(document.raw_text for document in persisted_documents),
            *(chunk.text for chunk in persisted_chunks),
            *(item.excerpt for item in persisted_evidence),
        ]:
            assert_no_oracle_text(value)


def test_debug_material_bundle_text_looks_like_real_documents(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="normal_f1_bundle",
    )
    raw_text_by_type = {
        document["document_type"]: document["raw_text"]
        for document in payload["documents"]
    }

    assert "Online Nonimmigrant Visa Application" in raw_text_by_type["ds160"]
    assert "Passport/Travel Document Number" in raw_text_by_type["ds160"]
    assert "PASSPORT BIOGRAPHIC PAGE - OCR TEXT" in raw_text_by_type["passport_bio"]
    assert "Certificate of Eligibility for Nonimmigrant Student Status" in raw_text_by_type["i20"]
    assert "Financials - Estimated average costs" in raw_text_by_type["i20"]
    assert "Office of Graduate Admission" in raw_text_by_type["admission_letter"]
    assert "Certificate of Deposit Balance - OCR Extract" in raw_text_by_type["funding_proof"]
    assert "Household Register Extract" in raw_text_by_type[
        "relationship_proof_between_applicant_and_sponsors"
    ]


def test_funding_shortfall_bundle_expresses_gap_through_amounts(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="funding_shortfall_bundle",
    )
    documents = {document["document_type"]: document for document in payload["documents"]}

    assert documents["i20"]["fields"]["/education/first_year_cost"] == "68000"
    assert documents["funding_proof"]["fields"]["/funding/available_funds"] == "9800"
    assert "Available Balance: USD 9800" in documents["funding_proof"]["raw_text"]
    assert_no_oracle_text(documents["funding_proof"]["raw_text"])


def test_sponsor_chain_gap_bundle_only_contains_partial_source_chain(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="sponsor_chain_gap_bundle",
    )
    funding_document = next(
        document
        for document in payload["documents"]
        if document["document_type"] == "funding_proof"
    )

    assert funding_document["fields"]["/funding/source_detail"] == (
        "family company equity transfer proceeds"
    )
    assert "Incoming Remittance and Balance Summary" in funding_document["raw_text"]
    assert "Company Name on Memo: Example Family Business LLC" in funding_document["raw_text"]
    assert "Missing" not in funding_document["raw_text"]
    persisted_filenames = {
        document["filename"].lower()
        for document in payload["documents"]
    }
    assert not any("transfer_agreement" in filename for filename in persisted_filenames)
    assert not any("tax" in filename for filename in persisted_filenames)
    assert not any("company_registration" in filename for filename in persisted_filenames)


def test_claim_vs_document_bundle_records_synthetic_turn_claim(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="claim_vs_document_bundle",
        include_synthetic_user_turns=True,
    )

    assert payload["synthetic_turns"][0]["field_claims"] == {
        "/funding/primary_source": "self"
    }
    record = db_session.get(SessionRecord, session_id)
    assert record is not None
    claim_history = record.profile_json["ds160_view"]["field_claim_history"]
    assert claim_history["/funding/primary_source"][0]["value"] == "self"


def test_seeded_material_bundle_uses_ai_generated_documents(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )

    def fake_generate(self, *, record, scenario, seed_text, include_synthetic_user_turns):
        assert seed_text == "我会去 New York University 读 MS Computer Science，父母资助。"
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
        ), {"generator": "stub"}

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="normal_f1_bundle",
        seed_text="我会去 New York University 读 MS Computer Science，父母资助。",
    )

    documents = {document["document_type"]: document for document in payload["documents"]}
    assert payload["generation"]["source"] == "ai"
    assert documents["i20"]["fields"]["/education/school_name"] == "New York University"
    assert documents["admission_letter"]["fields"]["/education/program_name"] == (
        "MS Computer Science"
    )


def test_material_bundle_uses_session_transcript_seed_when_request_seed_missing(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    captured: dict[str, str] = {}

    def fake_generate(self, *, record, scenario, seed_text, include_synthetic_user_turns):
        captured["seed_text"] = seed_text
        return GeneratedMaterialBundleOutput(
            documents=[
                {
                    "document_type": "ds160",
                    "filename": "ai_ds160.txt",
                    "raw_text": "Online Nonimmigrant Visa Application\nApplicant: TEST APPLICANT\n",
                    "fields": {"/identity/full_name": "TEST APPLICANT"},
                },
                {
                    "document_type": "passport_bio",
                    "filename": "ai_passport.txt",
                    "raw_text": "PASSPORT BIOGRAPHIC PAGE\nFull Name: TEST APPLICANT\n",
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
                    "raw_text": "Bank Balance Certificate\nAvailable Balance: USD 90000\n",
                    "fields": {"/funding/available_funds": "90000"},
                },
            ],
        ), {"generator": "stub"}

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )
    session_id = seed_session(db_session)
    SessionTurnRepository(db_session).append_user_turn(
        session_id=session_id,
        content="我会去 New York University 读 MS Computer Science，父母资助。",
        source="user_message",
    )

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="normal_f1_bundle",
    )

    assert "New York University" in captured["seed_text"]
    assert payload["generation"]["source"] == "ai"
    assert payload["generation"]["mode"] == "ai_if_available"
    assert payload["generation"]["seed_source"] == "session_transcript"


def test_seeded_material_bundle_fails_without_writing_demo_materials(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )

    def fake_generate(self, **kwargs):
        raise ModelRuntimeError(detail="stub provider unavailable", status_code=503)

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )
    session_id = seed_session(db_session)

    with pytest.raises(ModelRuntimeError) as exc_info:
        DebugMaterialBundleService(db_session).create_bundle(
            session_id,
            scenario="normal_f1_bundle",
            seed_text="我会去 New York University 读 MS Computer Science，父母资助。",
        )

    assert exc_info.value.status_code == 503
    assert "AI 材料生成失败，未写入任何演示占位材料" in exc_info.value.detail
    assert "stub provider unavailable" in exc_info.value.detail
    assert (
        db_session.query(DocumentRecord).filter_by(session_id=session_id).count()
        == 0
    )
    assert (
        db_session.query(DocumentChunkRecord).filter_by(session_id=session_id).count()
        == 0
    )
    assert (
        db_session.query(EvidenceItemRecord).filter_by(session_id=session_id).count()
        == 0
    )
