from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.core import settings as settings_module
from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord, SessionRecord
from app.db.session import get_db
from app.main import app
from app.services.capability_orchestrator import CapabilityOrchestrator


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
