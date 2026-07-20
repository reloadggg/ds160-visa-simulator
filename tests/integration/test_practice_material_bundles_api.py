"""Practice material bundles are a product feature (default ON), independent of debug."""

from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import AdminSettingRecord, DocumentRecord, SessionRecord
from app.db.session import get_db
from app.main import app
from tests.integration.test_debug_material_bundles_api import (
    SEED_TEXT,
    install_ai_material_generator_stub,
    install_material_refresh_stub,
    parse_sse_events,
)


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'practice-bundles-api.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    try:
        yield testing_session_local
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def _set_demo_settings(
    db: Session,
    *,
    console: bool = False,
    debug_materials: bool = False,
    practice_materials: bool | None = None,
) -> None:
    value_json: dict = {
        "model_base_url": None,
        "model_api_key": None,
        "model_name": None,
        "model_streaming_enabled": True,
        "user_model_config_enabled": False,
        "show_github_link": False,
        "wx_entry_enabled": False,
        "debug_console_enabled": console,
        "debug_material_enabled": debug_materials,
        "rag_status_user_visible": False,
    }
    # Omit key entirely to exercise product default ON migration path.
    if practice_materials is not None:
        value_json["practice_materials_enabled"] = practice_materials
    db.merge(AdminSettingRecord(setting_key="demo", value_json=value_json))
    db.commit()


@pytest.fixture()
def client(db_session_factory) -> Generator[TestClient, None, None]:
    # Product default: practice ON; debug OFF (no key stored → defaults ON).
    with db_session_factory() as db:
        _set_demo_settings(db, console=False, debug_materials=False)

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


def test_practice_material_bundle_api_works_when_enabled_by_default(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default demo settings omit practice_materials_enabled → product ON."""
    refresh_calls = install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/practice/material-bundles",
        json={"scenario": "funding_shortfall_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["scenario"] == "funding_shortfall_bundle"
    assert payload["is_practice_material"] is True
    assert isinstance(payload["user_summary_zh"], str)
    assert payload["user_summary_zh"].strip()
    assert isinstance(payload["document_briefs_zh"], list)
    assert len(payload["document_briefs_zh"]) >= 5
    assert len(payload["documents"]) >= 5
    assert refresh_calls == ["debug_material_bundle:funding_shortfall_bundle"]

    with db_session_factory() as db:
        documents = db.query(DocumentRecord).filter_by(session_id=session_id).all()
        evidence = db.query(EvidenceItemRecord).filter_by(session_id=session_id).all()
        record = db.get(SessionRecord, session_id)

    assert len(documents) == len(payload["documents"])
    assert any(item.field_path == "/funding/available_funds" for item in evidence)
    assert record is not None
    assert record.gate_status_json["status"] == "ready_for_interview"


def test_practice_material_bundle_stream_emits_progress_and_final(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    with client.stream(
        "POST",
        f"/v1/sessions/{session_id}/practice/material-bundles/stream",
        json={"scenario": "identity_mismatch_bundle", "seed_text": SEED_TEXT},
    ) as response:
        body = response.read().decode()

    assert response.status_code == 200
    assert "event: accepted" in body
    assert "event: document_created" in body
    assert "event: evidence_written" in body
    assert "event: final" in body
    assert "identity_mismatch_bundle" in body

    final_events = [data for event, data in parse_sse_events(body) if event == "final"]
    assert len(final_events) == 1
    final = final_events[0]
    assert final["is_practice_material"] is True
    assert isinstance(final.get("user_summary_zh"), str)
    assert final["user_summary_zh"].strip()
    assert isinstance(final.get("document_briefs_zh"), list)
    assert len(final["document_briefs_zh"]) >= 5


def test_practice_material_bundle_api_disabled_returns_403(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    with db_session_factory() as db:
        _set_demo_settings(
            db,
            console=False,
            debug_materials=False,
            practice_materials=False,
        )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/practice/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "practice materials are disabled"}
    with db_session_factory() as db:
        assert db.query(DocumentRecord).filter_by(session_id=session_id).count() == 0


def test_practice_material_bundle_stream_disabled_returns_403(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    with db_session_factory() as db:
        _set_demo_settings(
            db,
            console=False,
            debug_materials=False,
            practice_materials=False,
        )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/practice/material-bundles/stream",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )

    assert response.status_code == 403
    assert response.json() == {"detail": "practice materials are disabled"}


def test_practice_enabled_debug_disabled_keeps_debug_routes_gated(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Practice is product ON; debug material/console remain independently gated."""
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    with db_session_factory() as db:
        _set_demo_settings(
            db,
            console=False,
            debug_materials=False,
            practice_materials=True,
        )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    practice = client.post(
        f"/v1/sessions/{session_id}/practice/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )
    assert practice.status_code == 200
    assert practice.json()["is_practice_material"] is True

    debug_bundle = client.post(
        f"/v1/sessions/{session_id}/debug/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )
    assert debug_bundle.status_code == 403
    assert debug_bundle.json() == {"detail": "debug fill is disabled"}

    debug_runtime = client.get(f"/v1/sessions/{session_id}/debug/runtime")
    assert debug_runtime.status_code == 403
    assert debug_runtime.json() == {"detail": "runtime debug is disabled"}


def test_practice_route_still_allowed_when_only_debug_material_enabled(
    client: TestClient,
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """material_generation_enabled = practice OR debug_material (legacy path)."""
    install_material_refresh_stub(monkeypatch)
    install_ai_material_generator_stub(monkeypatch)
    with db_session_factory() as db:
        _set_demo_settings(
            db,
            console=True,
            debug_materials=True,
            practice_materials=False,
        )

    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    response = client.post(
        f"/v1/sessions/{session_id}/practice/material-bundles",
        json={"scenario": "normal_f1_bundle", "seed_text": SEED_TEXT},
    )
    assert response.status_code == 200
    assert response.json()["is_practice_material"] is True
    assert response.json()["user_summary_zh"].strip()
