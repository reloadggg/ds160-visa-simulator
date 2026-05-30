from collections.abc import Generator
import json
from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import (
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


def test_debug_material_bundle_api_persists_documents_and_evidence(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    refresh_calls = install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "funding_shortfall_bundle"},
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


def test_runtime_debug_snapshot_includes_material_generation_metadata(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    bundle_response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle"},
    )
    response = client.get(f"/v1/sessions/{session_id}/debug/runtime")

    assert bundle_response.status_code == 200
    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == "ds160.runtime_debug.v1"
    assert payload["backend"]["version"]
    assert payload["material_generation"]["scenario"] == "normal_f1_bundle"
    assert payload["material_generation"]["generation"]["source"] == "deterministic"
    assert payload["material_generation"]["generation"]["seed_source"] is None
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_debug_fill", False)
    monkeypatch.setattr(settings_module.settings, "allow_runtime_debug", False)
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
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/debug/material-bundles/stream",
        json={"scenario": "identity_mismatch_bundle"},
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
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={
            "scenario": "normal_f1_bundle",
            "seed_text": "我会去 New York University 读 MS Computer Science，父母资助。",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert captured == {
        "scenario": "normal_f1_bundle",
        "seed_text": "我会去 New York University 读 MS Computer Science，父母资助。",
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
        raise ModelRuntimeError(detail="stub provider returned 504", status_code=503)

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
            "seed_text": "我会去 New York University 读 MS Computer Science，父母资助。",
        },
    )

    assert response.status_code == 503
    assert "AI 材料生成失败，未写入任何演示占位材料" in response.json()["detail"]
    assert "stub provider returned 504" in response.json()["detail"]

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
        raise ModelRuntimeError(detail="stub provider returned 504", status_code=503)

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
            "seed_text": "我会去 New York University 读 MS Computer Science，父母资助。",
        },
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: error" in body
    assert "AI 材料生成失败，未写入任何演示占位材料" in body
    assert "stub provider returned 504" in body
    assert "event: final" not in body
    assert "event: document_created" not in body

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
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "claim_vs_document_bundle"},
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
        json={"scenario": "school_mismatch_bundle"},
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
    assert metadata["agent_runtime"] == "graph"
    assert metadata["selected_public_runtime"] == "native_interviewer"
    assert metadata["runtime_execution"] == {
        "schema_version": "runtime.execution.v1",
        "configured_runtime": "graph",
        "requested_public_runtime": "native_interviewer",
        "public_runtime": "native_interviewer",
        "execution_runtime": "native_interviewer_runtime",
        "runtime_engine": "native_interviewer_runtime",
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
        json={"scenario": "school_mismatch_bundle"},
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


def test_debug_material_bundle_graph_failure_preserves_persisted_materials(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "agent_runtime", "graph")
    monkeypatch.setattr(settings_module.settings, "agent_runtime_fail_open_to_legacy", True)
    monkeypatch.setattr(
        "app.services.native_interviewer_runtime_service.NativeInterviewerRuntimeService.run_material_change",
        lambda self, record, *, reason: (_ for _ in ()).throw(
            RuntimeError("native material refresh exploded")
        ),
    )

    def fake_legacy_refresh(self, record, *, reason: str) -> dict:
        record.phase_state = "interview"
        record.current_governor_decision = "continue_interview"
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "interview_question",
            "question": "Please continue with your study plan.",
        }
        record.interviewer_state_json = {
            "owner": "interviewer_runtime_service",
            "status": "continue_interview",
            "decision": "continue_interview",
            "governor_decision": "continue_interview",
            "current_focus": record.current_focus_json,
        }
        return {
            "assistant_message": "Please continue with your study plan.",
            "governor_decision": "continue_interview",
            "requested_documents": [],
            "remaining_required_documents": [],
            "turn_decision": {"decision": "continue_interview"},
            "document_review": {},
            "runtime_view_state": {},
            "prompt_trace": {},
        }

    monkeypatch.setattr(
        "app.services.interviewer_runtime_service.InterviewerRuntimeService.refresh_after_material_change",
        fake_legacy_refresh,
    )
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "claim_vs_document_bundle"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["assistant_message"] == "Please continue with your study plan."

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
    assert payload["material_refresh"]["graph_runtime_error"] == {
        "status": "error",
        "agent_runtime": "graph",
        "selected_public_runtime": "native_interviewer",
        "error_type": "RuntimeError",
        "error_message": "native material refresh exploded",
        "fallback_runtime": "legacy",
    }
    assert payload["material_refresh"]["selected_public_runtime"] == "legacy"
    assert payload["material_refresh"]["runtime_execution"]["public_runtime"] == "legacy"
    assert (
        payload["material_refresh"]["runtime_execution"]["fallback_runtime"]
        == "legacy"
    )


def test_debug_material_bundle_refresh_error_returns_final_payload(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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
        json={"scenario": "normal_f1_bundle"},
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
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "allow_debug_fill", False)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle"},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "debug fill is disabled"}
