import time
from collections.abc import Generator
import asyncio

from fastapi.testclient import TestClient
from fastapi import FastAPI
import fitz
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import DocumentRecord, SessionRecord
from app.db.session import get_db
from app.domain.runtime import build_initial_gate_status
from app.main import app
from app.workers.parse_worker import stop_parse_worker_runtime


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'parse-worker-runtime.sqlite3'}",
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
def client(
    db_session_factory,
    monkeypatch: pytest.MonkeyPatch,
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    monkeypatch.setenv("PARSE_WORKER_INLINE", "1")
    monkeypatch.setenv("PARSE_WORKER_POLL_INTERVAL_SECONDS", "0.01")
    app.state.parse_worker_session_factory = db_session_factory
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.parse_worker_session_factory = None


def test_parse_worker_runtime_automatically_processes_uploaded_documents(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="runtime_test",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement for tuition"),
                "application/pdf",
            )
        },
    )
    assert upload_response.status_code == 202
    document_id = upload_response.json()["document_id"]

    with db_session_factory() as db:
        waiting = db.get(SessionRecord, session_id)
        assert waiting is not None
        assert waiting.phase_state == "interview"
        assert waiting.gate_status_json["status"] == "pending_documents"

    deadline = time.monotonic() + 2.0
    completed_status = None
    completed_phase = None
    understanding_status = None
    while time.monotonic() < deadline:
        with db_session_factory() as db:
            record = db.get(SessionRecord, session_id)
            document = db.get(DocumentRecord, document_id)
            assert record is not None
            assert document is not None
            completed_phase = record.phase_state
            completed_status = record.gate_status_json["status"]
            understanding_status = document.artifact_json.get("understanding_status")
            if (
                completed_phase == "interview"
                and completed_status == "pending_documents"
                and understanding_status == "completed"
            ):
                break
        time.sleep(0.05)

    assert completed_phase == "interview"
    assert completed_status == "pending_documents"
    assert understanding_status == "completed"


@pytest.mark.asyncio
async def test_stop_parse_worker_runtime_cancels_stuck_task() -> None:
    async def never_finishes() -> None:
        await asyncio.Future()

    runtime_app = FastAPI()
    runtime_app.state.parse_worker_stop_event = asyncio.Event()
    runtime_app.state.parse_worker_task = asyncio.create_task(never_finishes())

    await stop_parse_worker_runtime(runtime_app)

    assert runtime_app.state.parse_worker_stop_event is None
    assert runtime_app.state.parse_worker_task is None
