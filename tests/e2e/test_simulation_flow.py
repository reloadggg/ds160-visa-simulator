from collections.abc import Generator
import asyncio

from fastapi.testclient import TestClient
import fitz
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
from types import SimpleNamespace

from app.db.base import Base
from app.db.models import SessionRecord
from app.db.session import get_db
from app.main import app
from app.workers.parse_worker import ParseWorker, stop_parse_worker_runtime


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def install_stub_build_question_action(
    monkeypatch: pytest.MonkeyPatch,
    *,
    continue_interview_message: str = "What is the purpose of your travel?",
) -> None:
    monkeypatch.setattr(
        "app.services.interview_runtime_service.InterviewRuntimeService.build_question_action",
        lambda self, session_id, profile, score, governor_decision, trace_entries, recent_turns=None: (
            SimpleNamespace(
                assistant_message=continue_interview_message,
                requested_documents=[],
                decision_hint="continue_interview",
            )
            if governor_decision == "continue_interview" and not score.missing_evidence
            else SimpleNamespace(
                assistant_message=(
                    f"Please upload {score.missing_evidence[0]}."
                    if score.missing_evidence
                    else "Please provide the key supporting document for this point."
                ),
                requested_documents=list(score.missing_evidence[:1]),
                decision_hint="need_more_evidence",
            )
        ),
    )


def upload_f1_gate_package(client: TestClient, session_id: str) -> None:
    files = {
        "ds160": (
            "ds160.pdf",
            build_pdf_bytes(
                "DS-160 Confirmation Page\n"
                "Full name: TEST APPLICANT\n"
                "Passport number: X00000000\n"
                "Travel purpose: STUDENT (F1)\n"
            ),
        ),
        "passport_bio": (
            "passport_bio.pdf",
            build_pdf_bytes(
                "Passport Bio Page\n"
                "Full name: TEST APPLICANT\n"
                "Passport number: X00000000\n"
                "Nationality: EXAMPLELAND\n"
            ),
        ),
        "i20": (
            "i20.pdf",
            build_pdf_bytes(
                "Form I-20\n"
                "SEVIS ID: N0000000000\n"
                "School name: Example University\n"
                "Program: Example Degree Program\n"
            ),
        ),
        "admission_letter": (
            "admission_letter.pdf",
            build_pdf_bytes(
                "Admission Letter\n"
                "School name: Example University\n"
                "Program: Example Degree Program\n"
            ),
        ),
        "funding_proof": (
            "funding_proof.pdf",
            build_pdf_bytes("Parent sponsor bank statement for tuition"),
        ),
    }
    for document_type, (filename, raw_bytes) in files.items():
        response = client.post(
            f"/v1/sessions/{session_id}/files",
            data={"document_type": document_type},
            files={"file": (filename, raw_bytes, "application/pdf")},
        )
        assert response.status_code == 202


def drain_parse_worker(db_session_factory) -> bool:
    with db_session_factory() as db:
        processed_any = False
        while ParseWorker(db).run_once():
            processed_any = True
    return processed_any


@pytest.fixture(autouse=True)
def disable_runtime_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPENAI_BASE_URL", raising=False)
    monkeypatch.setenv("PARSE_WORKER_INLINE", "0")


@pytest.fixture()
def db_session_factory(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'simulation-flow.sqlite3'}",
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
) -> Generator[TestClient, None, None]:
    def override_get_db() -> Generator[Session, None, None]:
        db = db_session_factory()
        try:
            yield db
        finally:
            db.close()

    asyncio.run(stop_parse_worker_runtime(app))
    app.state.parse_worker_session_factory = None
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    asyncio.run(stop_parse_worker_runtime(app))
    app.dependency_overrides.clear()
    app.state.parse_worker_session_factory = None


def test_golden_path_f1_parent_sponsored_progresses_after_helpful_upload(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_stub_build_question_action(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    first_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )

    assert first_response.status_code == 200
    first_payload = first_response.json()
    assert first_payload["governor_decision"] == "need_more_evidence"
    assert first_payload["requested_documents"] == ["funding_proof"]
    assert first_payload["remaining_required_documents"] == ["funding_proof"]
    assert any(
        item["document_type"] == "ds160"
        for item in first_payload["gate_progress"]["documents"]
    )

    upload_f1_gate_package(client, session_id)

    pre_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert pre_worker_response.status_code == 200
    assert pre_worker_response.json()["governor_decision"] == "need_more_evidence"
    assert (
        pre_worker_response.json()["gate_progress"]["overall_status"]
        == "waiting_for_parse"
    )

    assert drain_parse_worker(db_session_factory) is True

    post_worker_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert post_worker_response.status_code == 200
    post_worker_payload = post_worker_response.json()
    assert post_worker_payload["governor_decision"] == "continue_interview"
    assert post_worker_payload["requested_documents"] == []
    assert post_worker_payload["assistant_message"]

    user_report_response = client.get(f"/v1/sessions/{session_id}/reports/user")
    internal_report_response = client.get(f"/v1/sessions/{session_id}/reports/internal")

    assert user_report_response.status_code == 200
    assert internal_report_response.status_code == 200

    user_report = user_report_response.json()
    internal_report = internal_report_response.json()
    assert user_report["governor_decision"] == "continue_interview"
    assert user_report["interview_status"] == "continue_interview"
    assert user_report["outcome_label"] == "正式问答进行中"
    assert "正式 interview" in user_report["summary"]
    assert user_report["current_key_question"] == post_worker_payload["assistant_message"]
    assert internal_report["interviewer_state"]["decision"] == "continue_interview"
    assert (
        internal_report["interviewer_state"]["current_key_question"]
        == user_report["current_key_question"]
    )
    assert internal_report["current_focus"]["question"] == user_report[
        "current_key_question"
    ]


def test_irrelevant_upload_does_not_drift_mainline_focus(
    client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    install_stub_build_question_action(monkeypatch)
    session_resp = client.post("/v1/sessions", json={"declared_family": "f1"})

    assert session_resp.status_code == 201
    session_id = session_resp.json()["session_id"]

    first_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={
            "role": "user",
            "content": "My mother and father will cover all my tuition and living expenses.",
        },
    )

    assert first_response.status_code == 200
    assert first_response.json()["governor_decision"] == "need_more_evidence"
    assert first_response.json()["requested_documents"] == ["funding_proof"]
    assert first_response.json()["remaining_required_documents"] == ["funding_proof"]

    upload_response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={
            "file": (
                "tourism_flyer.pdf",
                build_pdf_bytes("Completely unrelated tourism flyer"),
                "application/pdf",
            )
        },
    )

    assert upload_response.status_code == 202
    upload_payload = upload_response.json()
    assert upload_payload["main_flow_feedback"]["status"] == "not_helpful"
    assert "没有直接帮助" in upload_payload["main_flow_feedback"]["message"]

    next_response = client.post(
        f"/v1/sessions/{session_id}/messages",
        json={"role": "user", "content": "I will study computer science."},
    )

    assert next_response.status_code == 200
    next_payload = next_response.json()
    assert next_payload["governor_decision"] == "need_more_evidence"
    assert next_payload["requested_documents"] == ["funding_proof"]
    assert next_payload["remaining_required_documents"] == ["funding_proof"]

    user_report_response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert user_report_response.status_code == 200
    user_report = user_report_response.json()
    assert user_report["governor_decision"] == "need_more_evidence"
    assert user_report["interview_status"] == "waiting_key_proof"
    assert user_report["current_key_proof"] == "funding_proof"
    assert "upload_key_proof" in user_report["allowed_next_actions"]


def test_openai_compat_reuses_session_and_advances_to_interview_after_parse(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    install_stub_build_question_action(monkeypatch)
    first_completion = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "user",
                    "content": "My mother and father will cover all my tuition and living expenses.",
                }
            ],
            "metadata": {"declared_family": "f1"},
        },
    )

    assert first_completion.status_code == 200
    first_payload = first_completion.json()
    session_id = first_payload["metadata"]["session_id"]
    assert first_payload["metadata"]["session_id"] == session_id
    assert first_payload["metadata"]["phase_state"] == "interview"
    assert first_payload["metadata"]["context_mode"] == "new_session"
    assert first_payload["metadata"]["governor_decision"] == "need_more_evidence"
    assert first_payload["metadata"]["requested_documents"] == ["funding_proof"]
    assert first_payload["metadata"]["remaining_required_documents"] == [
        "funding_proof"
    ]

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

    second_completion = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "assistant",
                    "content": first_payload["choices"][0]["message"]["content"],
                },
                {"role": "user", "content": "I will study computer science."},
            ],
            "metadata": {"session_id": session_id},
        },
    )

    assert second_completion.status_code == 200
    second_payload = second_completion.json()
    assert second_payload["metadata"]["session_id"] == session_id
    assert second_payload["metadata"]["phase_state"] == "interview"
    assert second_payload["metadata"]["context_mode"] == "existing_session"
    assert second_payload["metadata"]["governor_decision"] == "need_more_evidence"

    assert drain_parse_worker(db_session_factory) is True

    third_completion = client.post(
        "/v1/chat/completions",
        json={
            "model": "visa-simulator-v1",
            "messages": [
                {
                    "role": "assistant",
                    "content": second_payload["choices"][0]["message"]["content"],
                },
                {"role": "user", "content": "I will study computer science."},
            ],
            "metadata": {"session_id": session_id},
        },
    )

    assert third_completion.status_code == 200
    third_payload = third_completion.json()
    assert third_payload["metadata"]["session_id"] == session_id
    assert third_payload["metadata"]["phase_state"] == "interview"
    assert third_payload["metadata"]["context_mode"] == "existing_session"
    assert third_payload["choices"][0]["message"]["role"] == "assistant"
    assert third_payload["choices"][0]["message"]["content"]

    user_report_response = client.get(f"/v1/sessions/{session_id}/reports/user")

    assert user_report_response.status_code == 200
    user_report = user_report_response.json()
    assert user_report["interview_status"] == "continue_interview"
    assert user_report["current_key_proof"] is None

    with db_session_factory() as db:
        session_count = db.scalar(select(func.count()).select_from(SessionRecord))

    assert session_count == 1
