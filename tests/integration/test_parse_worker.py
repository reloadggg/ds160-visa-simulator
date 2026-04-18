from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.db.base import Base
from app.db.models import JobRecord, SessionRecord
from app.db.session import get_db
from app.main import app
from app.workers.parse_worker import ParseWorker


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'parse-worker.sqlite3'}",
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


def test_parse_worker_processes_uploaded_document_before_next_message(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    first_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    for filename, raw_bytes in [
        ("ds160.txt", b"Completed DS-160 form draft"),
        ("passport_bio.txt", b"Passport biographic page"),
        ("i20.txt", b"Form I-20 issued by school"),
        ("admission_letter.txt", b"University admission letter"),
        ("funding_proof.txt", b"Parent sponsor bank statement for tuition"),
    ]:
        upload_response = client.post(
            f"/v1/sessions/{session_id}/files",
            files={"file": (filename, raw_bytes, "text/plain")},
        )
        assert upload_response.status_code == 202

    assert first_response.status_code == 200
    assert first_response.json()["governor_decision"] == "need_more_evidence"

    pre_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert pre_worker_response.status_code == 200
    assert pre_worker_response.json()["governor_decision"] == "need_more_evidence"

    with db_session_factory() as db:
        while ParseWorker(db).run_once():
            pass

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        assert record.phase_state == "interview"
        assert record.gate_status_json["status"] == "ready_for_interview"

    post_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert post_worker_response.status_code == 200
    assert post_worker_response.json()["governor_decision"] == "continue_interview"


def test_parse_worker_claims_oldest_queued_job_first(
    client: TestClient,
    db_session_factory,
) -> None:
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})
    session_id = session_resp.json()["session_id"]

    client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "My parents will pay for my studies."},
    )

    first_upload = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof_1.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )
    second_upload = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "funding_proof_2.txt",
                b"Parent sponsor bank statement for tuition",
                "text/plain",
            )
        },
    )

    assert first_upload.status_code == 202
    assert second_upload.status_code == 202

    first_job_id = first_upload.json()["job_id"]
    second_job_id = second_upload.json()["job_id"]

    with db_session_factory() as db:
        assert ParseWorker(db).run_once() is True

    with db_session_factory() as db:
        first_job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == first_job_id),
        )
        second_job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == second_job_id),
        )

        assert first_job is not None
        assert second_job is not None
        assert first_job.job_id < second_job.job_id
        assert first_job.status == "completed"
        assert second_job.status == "queued"
