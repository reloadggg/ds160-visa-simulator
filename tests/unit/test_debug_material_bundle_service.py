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
from app.services.ai_material_bundle_generator_service import (
    GeneratedMaterialBundleOutput,
    find_oracle_text_marker,
)
from app.services.runtime_errors import ModelRuntimeError
from app.repositories.session_turn_repo import SessionTurnRepository

SEED_TEXT = "我会去 New York University 读 MS Computer Science，父母资助。"

ORACLE_TEXT_PHRASES = (
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
    assert find_oracle_text_marker(value) is None
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


def generated_bundle_for_scenario(
    scenario: str,
    *,
    include_synthetic_user_turns: bool = True,
) -> GeneratedMaterialBundleOutput:
    school_name = "New York University"
    admission_school = (
        "Columbia University"
        if scenario == "school_mismatch_bundle"
        else school_name
    )
    ds160_passport = "P12345678"
    passport_number = (
        "P87654321"
        if scenario == "identity_mismatch_bundle"
        else ds160_passport
    )
    first_year_cost = "68000"
    available_funds = "9800" if scenario == "funding_shortfall_bundle" else "90000"
    funding_fields = {
        "/funding/primary_source": "parents",
        "/funding/available_funds": available_funds,
        "/funding/sponsor_relationship": "parents",
    }
    funding_text = (
        "Incoming Remittance and Balance Summary - OCR Extract\n"
        "Account Holder: Li Wei and Zhang Min\n"
        "Primary Source of Support: parents\n"
        f"Available Balance: USD {available_funds}\n"
        "Recent Credit: USD 76000\n"
        "Remittance Memo: family company equity transfer proceeds\n"
        "Company Name on Memo: Horizon Robotics LLC\n"
    )
    if scenario == "sponsor_chain_gap_bundle":
        funding_fields["/funding/source_detail"] = (
            "family company equity transfer proceeds"
        )
    else:
        funding_text = (
            "Bank of China\n"
            "Certificate of Deposit Balance - OCR Extract\n"
            "Account Holder: Li Wei and Zhang Min\n"
            "Primary Source of Support: parents\n"
            f"Available Balance: USD {available_funds}\n"
        )

    synthetic_turns = []
    if scenario == "claim_vs_document_bundle" and include_synthetic_user_turns:
        synthetic_turns.append(
            {
                "role": "user",
                "content": (
                    "I am self-funded and will pay the tuition and living expenses "
                    "with my own savings."
                ),
                "field_claims": {"/funding/primary_source": "self"},
            }
        )

    return GeneratedMaterialBundleOutput(
        documents=[
            {
                "document_type": "ds160",
                "filename": "ai_ds160.txt",
                "raw_text": (
                    "Online Nonimmigrant Visa Application\n"
                    "Applicant Name Provided: Morgan Lee\n"
                    f"Passport/Travel Document Number: {ds160_passport}\n"
                    "Purpose: STUDENT (F1)\n"
                ),
                "fields": {
                    "/identity/full_name": "Morgan Lee",
                    "/identity/passport_number": ds160_passport,
                    "/visa_intent/travel_purpose": "STUDENT (F1)",
                },
            },
            {
                "document_type": "passport_bio",
                "filename": "ai_passport.txt",
                "raw_text": (
                    "PASSPORT BIOGRAPHIC PAGE - OCR TEXT\n"
                    "Full Name: Morgan Lee\n"
                    f"Passport No.: {passport_number}\n"
                    "Nationality: China\n"
                ),
                "fields": {
                    "/identity/full_name": "Morgan Lee",
                    "/identity/passport_number": passport_number,
                    "/identity/nationality": "China",
                },
            },
            {
                "document_type": "i20",
                "filename": "ai_i20.txt",
                "raw_text": (
                    "Certificate of Eligibility for Nonimmigrant Student Status\n"
                    f"School Name: {school_name}\n"
                    "Program of Study: MS Computer Science\n"
                    "Financials - Estimated average costs\n"
                    f"First Year Cost Total: USD {first_year_cost}\n"
                ),
                "fields": {
                    "/education/school_name": school_name,
                    "/education/program_name": "MS Computer Science",
                    "/education/first_year_cost": first_year_cost,
                },
            },
            {
                "document_type": "admission_letter",
                "filename": "ai_admission.txt",
                "raw_text": (
                    f"{admission_school}\n"
                    "Office of Graduate Admission\n"
                    "Program: MS Computer Science\n"
                ),
                "fields": {
                    "/education/school_name": admission_school,
                    "/education/program_name": "MS Computer Science",
                },
            },
            {
                "document_type": "funding_proof",
                "filename": "ai_funding.txt",
                "raw_text": funding_text,
                "fields": funding_fields,
            },
            {
                "document_type": "relationship_proof_between_applicant_and_sponsors",
                "filename": "ai_relationship.txt",
                "raw_text": (
                    "Household Register Extract\n"
                    "Applicant: Morgan Lee\n"
                    "Father: Li Wei\n"
                    "Mother: Zhang Min\n"
                    "Relationship: parents\n"
                ),
                "fields": {
                    "/identity/full_name": "Morgan Lee",
                    "/funding/sponsor_relationship": "parents",
                    "/family/parent_names": "Li Wei; Zhang Min",
                },
            },
        ],
        synthetic_turns=synthetic_turns,
    )


def install_ai_generator_stub(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_generate(self, *, record, scenario, seed_text, include_synthetic_user_turns):
        assert seed_text == SEED_TEXT
        return generated_bundle_for_scenario(
            scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
        ), {"generator": "stub"}

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )


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
    install_ai_generator_stub(monkeypatch)
    service = DebugMaterialBundleService(db_session)

    for index, scenario in enumerate(DEBUG_MATERIAL_BUNDLE_SCENARIOS):
        session_id = seed_session(db_session, str(index))
        payload = service.create_bundle(
            session_id,
            scenario=scenario,
            seed_text=SEED_TEXT,
        )

        assert payload["scenario"] == scenario
        assert payload["generation"]["source"] == "ai"
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
    install_ai_generator_stub(monkeypatch)
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="school_mismatch_bundle",
        seed_text=SEED_TEXT,
    )

    school_documents = {
        document["document_type"]: document["fields"].get("/education/school_name")
        for document in payload["documents"]
        if document["document_type"] in {"i20", "admission_letter"}
    }
    assert school_documents == {
        "i20": "New York University",
        "admission_letter": "Columbia University",
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
    install_ai_generator_stub(monkeypatch)
    service = DebugMaterialBundleService(db_session)

    for index, scenario in enumerate(DEBUG_MATERIAL_BUNDLE_SCENARIOS):
        session_id = seed_session(db_session, f"oracle-{index}")
        payload = service.create_bundle(
            session_id,
            scenario=scenario,
            seed_text=SEED_TEXT,
        )

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
    install_ai_generator_stub(monkeypatch)
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="normal_f1_bundle",
        seed_text=SEED_TEXT,
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
    install_ai_generator_stub(monkeypatch)
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="funding_shortfall_bundle",
        seed_text=SEED_TEXT,
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
    install_ai_generator_stub(monkeypatch)
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="sponsor_chain_gap_bundle",
        seed_text=SEED_TEXT,
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
    assert "Company Name on Memo: Horizon Robotics LLC" in funding_document["raw_text"]
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
    install_ai_generator_stub(monkeypatch)
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="claim_vs_document_bundle",
        include_synthetic_user_turns=True,
        seed_text=SEED_TEXT,
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
        assert seed_text == SEED_TEXT
        return generated_bundle_for_scenario(
            scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
        ), {"generator": "stub"}

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )
    session_id = seed_session(db_session)

    payload = DebugMaterialBundleService(db_session).create_bundle(
        session_id,
        scenario="normal_f1_bundle",
        seed_text=SEED_TEXT,
    )

    documents = {document["document_type"]: document for document in payload["documents"]}
    assert payload["generation"]["source"] == "ai"
    assert documents["i20"]["fields"]["/education/school_name"] == "New York University"
    assert documents["admission_letter"]["fields"]["/education/program_name"] == (
        "MS Computer Science"
    )


def test_material_bundle_without_request_seed_fails_without_writing_materials(
    db_session: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        lambda self, session_id, *, reason: {},
    )
    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("AI generation should only use explicit request seed text")
        ),
    )
    session_id = seed_session(db_session)
    SessionTurnRepository(db_session).append_user_turn(
        session_id=session_id,
        content=SEED_TEXT,
        source="user_message",
    )

    with pytest.raises(ModelRuntimeError) as exc_info:
        DebugMaterialBundleService(db_session).create_bundle(
            session_id,
            scenario="normal_f1_bundle",
        )

    assert exc_info.value.status_code == 422
    assert "请先填写材料生成依据" in exc_info.value.detail
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
            seed_text=SEED_TEXT,
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
