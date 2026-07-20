from collections.abc import Generator
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import (
    AdminSettingRecord,
    CaseMemorySnapshotRecord,
    DocumentRecord,
    SessionRecord,
    SessionTurnRecord,
)
from app.db.session import get_db
from app.main import app
from app.services.ai_material_bundle_generator_service import (
    GeneratedMaterialBundleOutput,
)
from app.services.capability_orchestrator import CapabilityOrchestrator
from app.services.native_interviewer_runtime_service import NativeInterviewerOutput
from app.services.runtime_errors import ModelRuntimeError


FIXTURES_DIR = Path(__file__).resolve().parents[2] / "fixtures"
SEED_TEXT = "我会去 New York University 读 MS Computer Science，父母资助。"


def parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for raw_event in body.strip().split("\n\n"):
        event_name = ""
        data: dict = {}
        for line in raw_event.splitlines():
            if line.startswith("event:"):
                event_name = line.removeprefix("event:").strip()
            if line.startswith("data:"):
                data = json.loads(line.removeprefix("data:").strip())
        if event_name:
            events.append((event_name, data))
    return events


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'debug-bundles-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


@pytest.fixture()
def client(db_session_factory) -> Generator[TestClient, None, None]:
    with db_session_factory() as db:
        _set_demo_debug_settings(db, console=True, materials=True)

    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()


def _set_demo_debug_settings(
    db: Session,
    *,
    console: bool,
    materials: bool,
) -> None:
    db.merge(
        AdminSettingRecord(
            setting_key="demo",
            value_json={
                "model_base_url": None,
                "model_api_key": None,
                "model_name": None,
                "model_streaming_enabled": True,
                "user_model_config_enabled": False,
                "show_github_link": False,
                "wx_entry_enabled": False,
                "debug_console_enabled": console,
                "debug_material_enabled": materials,
                "rag_status_user_visible": False,
            },
        )
    )
    db.commit()


def install_material_refresh_stub(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def fake_refresh(self, session_id: str, *, reason: str) -> dict:
        calls.append(reason)
        return {
            "assistant_message": "Please continue with your study plan.",
            "governor_decision": "continue_interview",
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "document_review": {},
            "runtime_view_state": {},
        }

    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        fake_refresh,
    )
    return calls


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
    if scenario == "sponsor_chain_gap_bundle":
        funding_fields["/funding/source_detail"] = (
            "family company equity transfer proceeds"
        )
        funding_text = (
            "Incoming Remittance and Balance Summary - OCR Extract\n"
            "Account Holder: Li Wei and Zhang Min\n"
            f"Available Balance: USD {available_funds}\n"
            "Remittance Memo: family company equity transfer proceeds\n"
            "Company Name on Memo: Horizon Robotics LLC\n"
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


def promote_source_session_as_validated_archive(
    db_session_factory,
    *,
    session_id: str,
    package_id: str,
) -> None:
    required_document_types = [
        "ds160",
        "passport_bio",
        "i20",
        "admission_letter",
        "funding_proof",
        "relationship_proof_between_applicant_and_sponsors",
    ]
    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=session_id).all()
        for document in documents:
            artifact = dict(document.artifact_json or {})
            metadata = dict(artifact.get("metadata") or {})
            metadata.update(
                {
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": package_id,
                    "demo_template_archive_source": True,
                    "archive_source_reason": "validated_f1_demo_material_package",
                    "source_validation_session_id": session_id,
                    "demo_template_id": "f1_parent_sponsored_demo_test_v1",
                    "validation_status": "passed",
                    "visa_family": "f1",
                    "intent": "pass_oriented_customer_demo",
                    "required_document_types": required_document_types,
                }
            )
            artifact["metadata"] = metadata
            document.artifact_json = artifact
        db.commit()


def install_ai_material_generator_stub(monkeypatch: pytest.MonkeyPatch) -> None:
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


def test_debug_material_bundle_api_persists_documents_and_evidence(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "funding_shortfall_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] == "funding_shortfall_bundle"
    assert len(payload["documents"]) >= 5
    assert payload["expected_findings"][0]["kind"] == "funding_shortfall"
    assert refresh_calls == ["debug_material_bundle:funding_shortfall_bundle"]

    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=session_id).all()
        evidence = db.query(EvidenceItemRecord).filter_by(session_id=session_id).all()
        record = db.get(SessionRecord, session_id)

    assert len(documents) == len(payload["documents"])
    assert any(item.field_path == "/funding/available_funds" for item in evidence)
    assert any(item.field_path == "/education/first_year_cost" for item in evidence)
    assert record is not None
    assert record.gate_status_json["status"] == "ready_for_interview"


def test_material_package_archive_lists_and_imports_only_validated_archive(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    source_session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    source_session_id = source_session_resp.json()["session_id"]

    bundle_response = client.post(
        f"/v1/sessions/{source_session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )

    assert bundle_response.status_code == 200
    bundle_payload = bundle_response.json()
    package_id = bundle_payload["bundle_id"]

    ordinary_list_response = client.get("/v1/material-packages")
    assert ordinary_list_response.status_code == 200
    assert ordinary_list_response.json()["packages"] == []

    promote_source_session_as_validated_archive(
        db_session_factory,
        session_id=source_session_id,
        package_id=package_id,
    )

    list_response = client.get("/v1/material-packages")
    assert list_response.status_code == 200
    packages = list_response.json()["packages"]
    package = next(item for item in packages if item["package_id"] == package_id)
    assert package["status"] == "ready"
    assert package["status_label"] == "可导入"
    assert package["document_count"] == len(bundle_payload["documents"])
    assert package["source_session_id"] == source_session_id
    assert package["validation_status"] == "passed"
    assert package["source_validation_session_id"] == source_session_id
    assert package["demo_template_id"] == "f1_parent_sponsored_demo_test_v1"
    assert package["archive_source_reason"] == "validated_f1_demo_material_package"
    assert package["intent"] == "pass_oriented_customer_demo"
    assert package["visa_family"] == "f1"

    target_session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    target_session_id = target_session_resp.json()["session_id"]
    import_response = client.post(
        f"/v1/sessions/{target_session_id}/material-packages/{package_id}/import"
    )

    assert import_response.status_code == 200
    import_payload = import_response.json()
    assert import_payload["session_id"] == target_session_id
    assert import_payload["package_id"] == package_id
    assert import_payload["import_status"] == "imported"
    assert import_payload["imported_bundle_id"] != package_id
    assert len(import_payload["documents"]) == len(bundle_payload["documents"])
    assert refresh_calls == [
        "debug_material_bundle:normal_f1_bundle",
        f"material_package_import:{package_id}",
    ]

    source_document_ids = {
        document["document_id"] for document in bundle_payload["documents"]
    }
    imported_document_ids = {
        document["document_id"] for document in import_payload["documents"]
    }
    assert imported_document_ids.isdisjoint(source_document_ids)

    with db_session_factory() as db:
        imported_documents = db.query(DocumentRecord).filter_by(
            session_id=target_session_id
        ).all()
        imported_chunks = db.query(DocumentChunkRecord).filter_by(
            session_id=target_session_id
        ).all()
        imported_evidence = db.query(EvidenceItemRecord).filter_by(
            session_id=target_session_id
        ).all()

    assert len(imported_documents) == len(bundle_payload["documents"])
    assert len(imported_chunks) == len(bundle_payload["documents"])
    assert len(imported_evidence) >= len(bundle_payload["documents"])
    assert all(
        document.artifact_json["metadata"]["material_package_import"]
        for document in imported_documents
    )
    assert all(
        document.artifact_json["metadata"]["archived_package_id"] == package_id
        for document in imported_documents
    )
    assert all(
        document.artifact_json["metadata"]["synthetic_bundle_id"]
        == import_payload["imported_bundle_id"]
        for document in imported_documents
    )

    list_after_import_response = client.get("/v1/material-packages")
    assert list_after_import_response.status_code == 200
    listed_package_ids = {
        item["package_id"] for item in list_after_import_response.json()["packages"]
    }
    assert package_id in listed_package_ids
    assert import_payload["imported_bundle_id"] not in listed_package_ids


def test_material_package_archive_rejects_debug_bundle_without_validation_session(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    source_session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    source_session_id = source_session_resp.json()["session_id"]

    bundle_response = client.post(
        f"/v1/sessions/{source_session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )

    assert bundle_response.status_code == 200
    package_id = bundle_response.json()["bundle_id"]
    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=source_session_id).all()
        for document in documents:
            artifact = dict(document.artifact_json or {})
            metadata = dict(artifact.get("metadata") or {})
            metadata.update(
                {
                    "debug_material_bundle": True,
                    "synthetic_bundle_id": package_id,
                    "demo_template_archive_source": True,
                    "archive_source_reason": "validated_f1_demo_material_package",
                    "validation_status": "passed",
                }
            )
            artifact["metadata"] = metadata
            document.artifact_json = artifact
        db.commit()

    list_response = client.get("/v1/material-packages")

    assert list_response.status_code == 200
    assert list_response.json()["packages"] == []


def test_material_package_import_rejects_partial_validated_archive(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    source_session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    source_session_id = source_session_resp.json()["session_id"]
    bundle_response = client.post(
        f"/v1/sessions/{source_session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )
    assert bundle_response.status_code == 200
    package_id = bundle_response.json()["bundle_id"]
    promote_source_session_as_validated_archive(
        db_session_factory,
        session_id=source_session_id,
        package_id=package_id,
    )

    with db_session_factory() as db:
        source_document = db.query(DocumentRecord).filter_by(
            session_id=source_session_id,
            filename="ai_funding.txt",
        ).one()
        source_document.artifact_json = {
            **source_document.artifact_json,
            "understanding_status": "failed",
        }
        db.commit()

    list_response = client.get("/v1/material-packages")
    assert list_response.status_code == 200
    package = next(
        item
        for item in list_response.json()["packages"]
        if item["package_id"] == package_id
    )
    assert package["status"] == "partial"
    assert package["validation_status"] == "incomplete"

    target_session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    target_session_id = target_session_resp.json()["session_id"]
    import_response = client.post(
        f"/v1/sessions/{target_session_id}/material-packages/{package_id}/import"
    )

    assert import_response.status_code == 409
    detail = import_response.json()["detail"]
    assert detail["package_id"] == package_id
    assert detail["package_status"] == "partial"
    assert "理解状态不完整" in detail["detail"]
    with db_session_factory() as db:
        assert (
            db.query(DocumentRecord).filter_by(session_id=target_session_id).count()
            == 0
        )


def test_debug_material_bundle_api_rejects_missing_seed_without_writing_materials(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("AI generation requires explicit request seed text")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle"},
    )

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["status"] == 422
    assert "请先填写材料生成依据" in detail["detail"]
    with db_session_factory() as db:
        assert db.query(DocumentRecord).filter_by(session_id=session_id).count() == 0
        assert (
            db.query(EvidenceItemRecord).filter_by(session_id=session_id).count()
            == 0
        )
        assert (
            db.query(SessionTurnRecord).filter_by(session_id=session_id).count()
            == 0
        )


def test_runtime_debug_snapshot_includes_material_generation_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    bundle_response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )
    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert bundle_response.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "ds160.runtime_debug.v1"
    assert payload["backend"]["version"]
    assert payload["material_generation"]["scenario"] == "normal_f1_bundle"
    assert payload["material_generation"]["generation"]["source"] == "ai"
    assert payload["material_generation"]["generation"]["seed_source"] == "request"
    assert payload["timeline"][0]["phase"] == "material_generation"
    assert payload["timeline"][0]["step"] == "debug_material_bundle"
    assert payload["timeline"][0]["status"] == "completed"
    assert "scenario=normal_f1_bundle" in payload["timeline"][0]["summary"]


def test_runtime_debug_snapshot_includes_material_understanding_failures(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        db.add(
            DocumentRecord(
                document_id="doc-failed-understanding",
                session_id=session_id,
                filename="broken.pdf",
                status="uploaded",
                artifact_json={
                    "document_type": "funding_proof",
                    "understanding_status": "failed",
                    "understanding_error": {
                        "code": "parse_failed",
                        "message": "RuntimeError before material understanding.",
                    },
                    "case_board_delta": {
                        "latest_material": {
                            "document_id": "doc-failed-understanding",
                            "filename": "broken.pdf",
                            "understanding_status": "failed",
                            "unknowns": [
                                "RuntimeError before material understanding.",
                            ],
                        }
                    },
                },
            )
        )
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert response.status_code == 200
    payload = response.json()
    expected_contract = json.loads(
        (
            FIXTURES_DIR
            / "runtime_debug"
            / "material_understanding_failure_snapshot_contract.json"
        ).read_text(encoding="utf-8")
    )
    assert payload["material_understanding"] == expected_contract[
        "material_understanding"
    ]
    assert {
        "source": "material_understanding",
        "document_id": "doc-failed-understanding",
        "filename": "broken.pdf",
        "code": "parse_failed",
        "message": "RuntimeError before material understanding.",
    } in payload["errors"]
    for expected_timeline_item in expected_contract["timeline"]:
        assert expected_timeline_item in payload["timeline"]


def test_runtime_debug_snapshot_includes_case_memory_and_evidence_graph(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        db.add(
            CaseMemorySnapshotRecord(
                session_id=session_id,
                snapshot_json={
                    "claims": [
                        {
                            "claim_id": "claim-school",
                            "field_path": "/education/school_name",
                            "value": "Example University",
                            "status": "documented",
                            "supporting_evidence_ids": ["ev-school"],
                            "confidence": 0.9,
                        }
                    ],
                    "evidence_cards": [
                        {
                            "evidence_id": "ev-school",
                            "source_type": "uploaded_file",
                            "document_id": "doc-i20",
                            "excerpt": "School Name: Example University",
                            "claim_refs": ["claim-school"],
                            "confidence": 0.9,
                        }
                    ],
                    "proof_points": [
                        {
                            "proof_point_id": "proof-school",
                            "visa_family": "f1",
                            "question": "Which school will you attend?",
                            "status": "supported",
                            "why_it_matters": "School identity anchors the case.",
                            "claim_refs": ["claim-school"],
                            "evidence_refs": ["ev-school"],
                        }
                    ],
                    "conflicts": [],
                    "next_move": {
                        "move_type": "ask",
                        "question": "Which program will you study?",
                        "reason": "Program detail is still unknown.",
                        "claim_refs": ["claim-school"],
                        "evidence_refs": ["ev-school"],
                    },
                },
            )
        )
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert response.status_code == 200
    payload = response.json()
    assert payload["case_board"]["claims"][0]["claim_id"] == "claim-school"
    assert payload["evidence_graph"]["claims"][0]["field_path"] == (
        "/education/school_name"
    )
    assert payload["evidence_graph"]["next_move"]["question"] == (
        "Which program will you study?"
    )


def test_runtime_debug_snapshot_redacts_sensitive_metadata(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.interviewer_state_json = {
            "api_key": "secret-api-key",
            "nested": {"access_token": "secret-token"},
        }
        db.add(
            SessionTurnRecord(
                turn_id="turn-redaction-test",
                turn_index=1,
                session_id=session_id,
                role="assistant",
                content="ok",
                source="test",
                metadata_json={
                    "runtime_view_state": {
                        "source_turn_id": "turn-redaction-test",
                        "decision": "continue_interview",
                        "governor_decision": "continue_interview",
                    },
                    "model_config": {"api_key": "secret-model-key"},
                },
            )
        )
        db.commit()

    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert response.status_code == 200
    serialized = json.dumps(response.json(), ensure_ascii=False)
    assert "secret-api-key" not in serialized
    assert "secret-token" not in serialized
    assert "secret-model-key" not in serialized
    assert "[redacted]" in serialized


def test_runtime_debug_snapshot_respects_debug_switch(
    client: TestClient,
    db_session_factory,
) -> None:
    with db_session_factory() as db:
        _set_demo_debug_settings(db, console=False, materials=True)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert response.status_code == 403
    assert response.json() == {"detail": "runtime debug is disabled"}


def test_debug_material_bundle_stream_emits_progress_and_final(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/debug/material-bundles/stream",
        json={"scenario": "identity_mismatch_bundle", "seed_text": SEED_TEXT},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: debug_bundle_started" in body
    assert "event: document_created" in body
    assert "event: evidence_written" in body
    assert "event: final" in body
    assert "identity_mismatch_bundle" in body


def test_debug_material_bundle_api_accepts_seeded_ai_generation(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    captured: dict[str, str] = {}

    def fake_generate(self, *, record, scenario, seed_text, include_synthetic_user_turns):
        captured["scenario"] = scenario
        captured["seed_text"] = seed_text
        return generated_bundle_for_scenario(
            scenario,
            include_synthetic_user_turns=include_synthetic_user_turns,
        ), {"generator": "stub"}

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fake_generate,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={
            "scenario": "normal_f1_bundle",
            "seed_text": SEED_TEXT,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured == {
        "scenario": "normal_f1_bundle",
        "seed_text": SEED_TEXT,
    }
    assert payload["generation"]["source"] == "ai"
    assert payload["documents"][2]["fields"]["/education/school_name"] == (
        "New York University"
    )


def test_debug_material_bundle_api_returns_error_when_seeded_ai_generation_fails(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)

    def fail_generate(self, **kwargs):
        raise ModelRuntimeError(
            detail="stub provider returned malformed JSON",
            status_code=502,
            provider="openai_compatible",
            model="claude-sonnet-4-6",
            upstream_code="model_output_invalid",
            error_category="model_output_invalid",
        )

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fail_generate,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={
            "scenario": "normal_f1_bundle",
            "seed_text": SEED_TEXT,
        },
    )

    assert response.status_code == 502
    detail = response.json()["detail"]
    assert detail["status"] == 502
    assert detail["error_category"] == "model_output_invalid"
    assert detail["upstream_code"] == "model_output_invalid"
    assert detail["provider"] == "openai_compatible"
    assert detail["model"] == "claude-sonnet-4-6"
    assert "AI 材料生成失败，未写入任何演示占位材料" in detail["detail"]
    assert "stub provider returned malformed JSON" in detail["detail"]

    with db_session_factory() as db:
        assert db.query(DocumentRecord).filter_by(session_id=session_id).count() == 0
        assert (
            db.query(EvidenceItemRecord).filter_by(session_id=session_id).count()
            == 0
        )
        assert (
            db.query(SessionTurnRecord).filter_by(session_id=session_id).count()
            == 0
        )


def test_debug_material_bundle_stream_returns_error_when_seeded_ai_generation_fails(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)

    def fail_generate(self, **kwargs):
        raise ModelRuntimeError(
            detail="stub provider returned malformed JSON",
            status_code=502,
            provider="openai_compatible",
            model="claude-sonnet-4-6",
            upstream_code="model_output_invalid",
            error_category="model_output_invalid",
        )

    monkeypatch.setattr(
        "app.services.debug_material_bundle_service.AIMaterialBundleGeneratorService.generate",
        fail_generate,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/debug/material-bundles/stream",
        json={
            "scenario": "normal_f1_bundle",
            "seed_text": SEED_TEXT,
        },
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: error" in body
    assert "AI 材料生成失败，未写入任何演示占位材料" in body
    assert "stub provider returned malformed JSON" in body
    assert "event: final" not in body
    assert "event: document_created" not in body
    error_events = [
        data for event, data in parse_sse_events(body) if event == "error"
    ]
    assert error_events == [
        {
            "status": 502,
            "detail": (
                "AI 材料生成失败，未写入任何演示占位材料。"
                "请稍后重试或更换模型。原始错误："
                "stub provider returned malformed JSON"
            ),
            "error_category": "model_output_invalid",
            "upstream_code": "model_output_invalid",
            "provider": "openai_compatible",
            "model": "claude-sonnet-4-6",
        }
    ]

    with db_session_factory() as db:
        assert db.query(DocumentRecord).filter_by(session_id=session_id).count() == 0
        assert (
            db.query(EvidenceItemRecord).filter_by(session_id=session_id).count()
            == 0
        )
        assert (
            db.query(SessionTurnRecord).filter_by(session_id=session_id).count()
            == 0
        )


def test_claim_vs_document_bundle_fallback_detects_claim_history_conflict(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "claim_vs_document_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        review_context = CapabilityOrchestrator(db)._build_document_review_context(
            session_id=session_id,
            dynamic_turn_context={
                "profile_snapshot": record.profile_json,
                "declared_family": record.declared_family,
            },
            evidence_digest={},
            focus_thread={},
            advisory_context={},
            gate_progress=record.gate_status_json,
        )
        review = CapabilityOrchestrator(db)._fallback_document_review_from_context(
            review_context,
        )

    assert review is not None
    assert review["review_status"] == "high_risk"
    assert review["recommended_next_step"] == "high_risk_review"
    assert review["claim_conflicts"][0]["field_paths"] == ["/funding/primary_source"]
    serialized_context = str(review_context)
    assert "expected_findings" not in serialized_context
    assert "claim_vs_document_bundle" not in serialized_context


def test_debug_material_bundle_graph_runtime_does_not_leak_oracle_context(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_ai_material_generator_stub(monkeypatch)

    def legacy_refresh_must_not_run(self, record, *, reason: str) -> dict:
        raise AssertionError("legacy material refresh should not run in graph mode")

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.refresh_after_material_change",
        legacy_refresh_must_not_run,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "school_mismatch_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] == "school_mismatch_bundle"
    assert payload["turn_decision"]["decision"] == "high_risk_review"
    assert payload["turn_decision"]["assistant_message_author"] == "native_interviewer"
    assert payload["material_refresh"]["assistant_turn_created"] is False
    assert payload["material_refresh"]["prompt_trace"]["native_trigger"] == "material_change"
    assert (
        payload["material_refresh"]["prompt_trace"]["material_change_reason"]
        == "materials_updated"
    )

    with db_session_factory() as db:
        assistant_count = db.scalar(
            select(func.count())
            .select_from(SessionTurnRecord)
            .where(
                SessionTurnRecord.session_id == session_id,
                SessionTurnRecord.role == "assistant",
            )
        )
        record = db.get(SessionRecord, session_id)
        review_context = CapabilityOrchestrator(db)._build_document_review_context(
            session_id=session_id,
            dynamic_turn_context={
                "profile_snapshot": record.profile_json if record else {},
                "declared_family": record.declared_family if record else None,
            },
            evidence_digest={},
            focus_thread={},
            advisory_context={},
            gate_progress=record.gate_status_json if record else {},
        )

    assert assistant_count == 0
    assert record is not None
    metadata = record.interviewer_state_json["last_material_refresh"]
    assert metadata["agent_runtime"] == "native_interviewer"
    assert metadata["selected_public_runtime"] == "native_interviewer"
    assert metadata["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
        "canonical_runtime": "native_interviewer",
        "runtime_role": "canonical",
        "canonical": True,
        "source": "material_change",
        "fail_open_to_legacy": False,
        "compatibility_runtime_label": "graph",
    }
    assert metadata["governor_decision"] == "high_risk_review"
    assert metadata["prompt_trace"]["native_trigger"] == "material_change"
    assert metadata["prompt_trace"]["material_change_reason"] == "materials_updated"
    serialized_graph_metadata = str(
        {
            "prompt_trace": metadata.get("prompt_trace"),
            "document_review": metadata.get("document_review"),
        }
    )
    assert "expected_findings" not in serialized_graph_metadata
    assert "school_mismatch_bundle" not in serialized_graph_metadata
    assert "学校材料冲突包" not in serialized_graph_metadata
    assert "dbg-bundle-" not in serialized_graph_metadata

    snapshot_response = client.get(f"/v1/sessions/{session_id}/debug/runtime")
    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["current_runtime"]["material_runtime_execution"] == metadata[
        "runtime_execution"
    ]
    assert (
        snapshot["current_runtime"]["execution_runtime"]
        == "native_interviewer_runtime"
    )
    serialized_review_context = str(review_context)
    assert "expected_findings" not in serialized_review_context
    assert "school_mismatch_bundle" not in serialized_review_context
    assert "学校材料冲突包" not in serialized_review_context
    assert "dbg-bundle-" not in serialized_review_context


def test_debug_material_bundle_native_prompt_does_not_leak_oracle_context(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_ai_material_generator_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService._build_runtime",
        lambda self, declared_family: {
            "provider": "openai_compatible",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
        },
    )
    captured_prompts: list[str] = []

    def fake_run(self, **kwargs):
        captured_prompts.append(kwargs["prompt"])
        return NativeInterviewerOutput(
            assistant_message="我会继续围绕你的 DS-160 材料做下一步核对。",
            decision="continue_interview",
        )

    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.OpenAIAgentsInterviewerRunner.run",
        fake_run,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    bundle_response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "school_mismatch_bundle", "seed_text": SEED_TEXT},
    )

    assert bundle_response.status_code == 200
    response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "我想继续说明我的学校材料。"},
    )

    assert response.status_code == 200
    assert len(captured_prompts) == 1
    prompt_payload = json.loads(captured_prompts[0])
    assert prompt_payload["current_user_message"] == "我想继续说明我的学校材料。"
    assert prompt_payload["interview_context"]["context_policy"][
        "legacy_extracted_hints_are_untrusted"
    ] is True
    serialized_prompt = captured_prompts[0]
    assert "debug_material_bundle" in serialized_prompt
    assert "expected_findings" not in serialized_prompt
    assert "synthetic_bundle_id" not in serialized_prompt
    assert "dbg-bundle-" not in serialized_prompt
    assert "debug_bundle_scenario" not in serialized_prompt
    assert "school_mismatch_bundle" not in serialized_prompt
    assert "学校材料冲突包" not in serialized_prompt


def test_debug_material_bundle_graph_failure_preserves_materials_without_legacy_fallback(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    install_ai_material_generator_stub(monkeypatch)
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            RuntimeError("native material refresh exploded")
        ),
    )

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.refresh_after_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            AssertionError("legacy material refresh should not run after native failure")
        ),
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "claim_vs_document_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] is None
    assert payload["material_refresh"] == {}
    assert payload["main_flow_refresh_error"] == (
        "RuntimeError: native material refresh exploded"
    )

    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=session_id).all()
        evidence = db.query(EvidenceItemRecord).filter_by(session_id=session_id).all()
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert len(documents) == len(payload["documents"])
    assert len(evidence) >= len(payload["documents"])
    assert any(turn.source == "debug_material_bundle" for turn in turns)
    assert all(turn.role != "assistant" for turn in turns)


def test_debug_material_bundle_refresh_error_returns_final_payload(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_ai_material_generator_stub(monkeypatch)

    def fail_refresh(self, session_id: str, *, reason: str) -> dict:
        raise RuntimeError("session turn index conflict")

    monkeypatch.setattr(
        "app.services.message_service.MessageService.refresh_after_material_change",
        fail_refresh,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert len(payload["documents"]) >= 5
    assert payload["main_flow_refresh_error"] == (
        "RuntimeError: session turn index conflict"
    )

    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=session_id).all()
        turns = db.scalars(
            select(SessionTurnRecord)
            .where(SessionTurnRecord.session_id == session_id)
            .order_by(SessionTurnRecord.turn_index)
        ).all()

    assert len(documents) == len(payload["documents"])
    assert all(turn.role != "assistant" for turn in turns)


def test_debug_material_bundle_rejects_unknown_scenario(
    client: TestClient,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "nope"},
    )

    assert response.status_code == 422
    assert "unsupported debug material bundle scenario" in response.json()["detail"]


def test_debug_material_bundle_respects_debug_switch(
    client: TestClient,
    db_session_factory,
) -> None:
    with db_session_factory() as db:
        _set_demo_debug_settings(db, console=True, materials=False)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "debug fill is disabled"}


def test_material_package_archive_respects_debug_switch(
    client: TestClient,
    db_session_factory,
) -> None:
    with db_session_factory() as db:
        _set_demo_debug_settings(db, console=True, materials=False)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    list_response = client.get("/v1/material-packages")
    import_response = client.post(
        f"/v1/sessions/{session_id}/material-packages/pkg-test/import"
    )

    expected = {
        "detail": (
            "material package archive is disabled because debug fill is disabled"
        )
    }
    assert list_response.status_code == 403
    assert list_response.json() == expected
    assert import_response.status_code == 403
    assert import_response.json() == expected
