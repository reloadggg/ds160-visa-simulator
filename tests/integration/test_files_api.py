from collections.abc import Generator

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session, sessionmaker
import fitz

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.db.session import get_db
from app.domain.case_memory import (
    CaseClaim,
    DocumentTypeCandidate,
    EvidenceCard,
    MaterialUnderstandingResult,
)
from app.domain.runtime import build_initial_gate_status
from app.main import app
from app.core import settings as settings_module


ORIGIN = "http://testserver"


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
        f"sqlite:///{tmp_path / 'files-api.sqlite3'}",
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
    app.state.auth_session_factory = db_session_factory
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
    app.state.auth_session_factory = None


def seed_session(db_session_factory, session_id: str) -> None:
    with db_session_factory() as db:
        db.add(SessionRecord(session_id=session_id, declared_family="f1"))
        db.commit()


def test_upload_file_creates_document_and_job(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = "sess-existing"
    raw_bytes = build_pdf_bytes("SEVIS ID: N1234567890")
    seed_session(db_session_factory, session_id)

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        files={"file": ("i20.pdf", raw_bytes, "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_status"] == "uploaded"
    assert payload["job_status"] == "queued"
    assert payload["understanding_status"] == "queued"
    assert payload["document_assessment"].get("document_type") is None
    assert payload["document_type_candidates"] == []
    assert payload["supported_claims"] == []
    assert payload["case_board_delta"]["latest_material"]["document_id"] == (
        payload["document_id"]
    )
    assert payload["case_board_delta"]["latest_material"]["understanding_status"] == "queued"
    assert payload["case_board_delta"]["latest_material"]["unknowns"] == [
        "案例理解任务已创建，视觉材料的完整证据、冲突和追问建议仍在更新。"
    ]
    assert payload["case_board_delta"]["conflicts"] == []
    assert payload["case_board_delta"]["next_move"] == {
        "move_type": "ask",
        "question": "请继续回答面签问题；材料理解完成后我会结合证据调整追问。",
        "reason": "文件已保存并进入案例理解队列，当前可以继续面签对话。",
        "claim_refs": [],
        "evidence_refs": [],
    }
    assert payload["case_board_refresh"] == {
        "event_type": "material_uploaded",
        "document_id": payload["document_id"],
        "status": "queued",
        "understanding_status": "queued",
        "failure_node": None,
        "failure_message": None,
        "debug_timeline_scope": {
            "session_id": session_id,
            "document_id": payload["document_id"],
            "scope": "material_understanding",
        },
        "message_policy": "case_board_timeline_only",
    }

    with db_session_factory() as db:
        document = db.scalar(
            select(DocumentRecord).where(
                DocumentRecord.document_id == payload["document_id"],
            ),
        )
        job = db.scalar(
            select(JobRecord).where(JobRecord.job_id == payload["job_id"]),
        )

        assert document is not None
        assert document.session_id == session_id
        assert document.filename == "i20.pdf"
        assert document.raw_bytes == raw_bytes
        assert document.raw_text == ""
        assert document.status == "uploaded"

        assert job is not None
        assert job.kind == "case_understanding"
        assert job.status == "queued"
        assert job.payload_json["document_id"] == document.document_id


def test_upload_file_rejects_missing_session(
    client: TestClient,
    db_session_factory,
) -> None:
    response = client.post(
        "/v1/sessions/sess-missing/files",
        files={"file": ("passport_bio.pdf", build_pdf_bytes("US visitor"), "application/pdf")},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Session not found: sess-missing"

    with db_session_factory() as db:
        document_count = db.scalar(select(func.count()).select_from(DocumentRecord))
        job_count = db.scalar(select(func.count()).select_from(JobRecord))

        assert document_count == 0
        assert job_count == 0


def test_upload_file_rejects_payload_over_64mb(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    seed_session(db_session_factory, "sess-existing")
    monkeypatch.setattr("app.services.file_service.MAX_UPLOAD_SIZE_BYTES", 4)

    response = client.post(
        "/v1/sessions/sess-existing/files",
        files={"file": ("large.pdf", b"12345", "application/pdf")},
    )

    assert response.status_code == 413
    assert response.json()["detail"] == "Uploaded file exceeds 64MB limit"


def test_upload_file_rejects_unsupported_media_type(
    client: TestClient,
    db_session_factory,
) -> None:
    seed_session(db_session_factory, "sess-existing")

    response = client.post(
        "/v1/sessions/sess-existing/files",
        files={"file": ("notes.txt", b"plain text", "text/plain")},
    )

    assert response.status_code == 415
    assert response.json()["detail"] == "Only PDF and PNG/JPG/JPEG images are supported"


def test_upload_file_returns_feedback_message_for_document_type(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    seed_session(db_session_factory, "sess-existing")

    def fake_extract(self, **kwargs):
        return None

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        fake_extract,
    )

    response = client.post(
        "/v1/sessions/sess-existing/files",
        data={"document_type": "passport_bio"},
        files={"file": ("passport_bio.pdf", build_pdf_bytes("Passport page"), "application/pdf")},
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_type"] == "passport_bio"
    assert payload["document_assessment"]["document_type"] == "passport_bio"
    assert "passport_bio" in payload["feedback_message"]


def test_upload_file_reports_helpful_feedback_for_current_key_proof(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-current-proof"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="upload-helpful",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "funding-proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "waiting_for_parse"
    assert payload["main_flow_feedback"] == {
        "status": "helpful",
        "supported_document_type": "funding_proof",
        "current_focus_document_type": "funding_proof",
        "message": (
            "这份材料已加入案例证据，候选证明点为 funding_proof。"
            "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
        ),
    }
    assert payload["document_assessment"]["main_flow_feedback"] == payload["main_flow_feedback"]


def test_upload_file_reports_partial_help_and_keeps_current_primary_focus(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-partial-help"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="upload-partial-help",
            required_documents=["ds160", "funding_proof"],
        )
        db.add(record)
        db.commit()

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "funding-proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "waiting_for_parse"
    assert payload["main_flow_feedback"] == {
        "status": "helpful",
        "supported_document_type": "funding_proof",
        "current_focus_document_type": "funding_proof",
        "message": (
            "这份材料已加入案例证据，候选证明点为 funding_proof。"
            "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
        ),
    }
    assert payload["document_assessment"]["main_flow_feedback"] == payload["main_flow_feedback"]


def test_upload_file_prefers_interviewer_focus_over_gate_primary_in_feedback(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-interviewer-focus"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.declared_family = "j1"
        record.gate_status_json = build_initial_gate_status(
            declared_family="j1",
            scenario_key="interviewer-focus-feedback",
            required_documents=["ds160", "ds2019"],
        )
        record.current_focus_json = {
            "owner": "interviewer_runtime_service",
            "kind": "required_document",
            "document_type": "ds2019",
        }
        record.interviewer_state_json = {
            "requested_documents": ["ds2019"],
        }
        db.add(record)
        db.commit()

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "ds2019"},
        files={
            "file": (
                "ds2019.pdf",
                build_pdf_bytes("SEVIS sponsor form"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_documents"] == ["ds2019"]
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "waiting_for_parse"
    assert payload["main_flow_feedback"] == {
        "status": "helpful",
        "supported_document_type": "ds2019",
        "current_focus_document_type": "ds2019",
        "message": (
            "这份材料已加入案例证据，候选证明点为 ds2019。"
            "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
        ),
    }
    assert payload["document_assessment"]["main_flow_feedback"] == payload["main_flow_feedback"]


def test_upload_file_can_use_backend_context_text_hint_without_frontend_document_type(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-context-hint"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.declared_family = "j1"
        record.gate_status_json = build_initial_gate_status(
            declared_family="j1",
            scenario_key="backend-context-hint",
            required_documents=["ds160", "passport_bio", "ds2019", "funding_proof"],
        )
        db.add(record)
        db.commit()

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"context_text": "这是我的 DS-2019 表。"},
        files={
            "file": (
                "upload.pdf",
                build_pdf_bytes("SEVIS sponsor form"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["document_type"] == "ds2019"
    assert payload["document_assessment"]["document_type_hint"] == "ds2019"


def test_upload_file_queues_explicit_type_without_blocking_on_relevance_model(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-fast-upload"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="upload-not-helpful",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: (_ for _ in ()).throw(
            AssertionError("upload response must not call extraction model")
        ),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "funding_proof"},
        files={
            "file": (
                "funding-proof.pdf",
                build_pdf_bytes("Tourism flyer"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "waiting_for_parse"
    assert payload["gate_progress"]["uploaded_count"] == 1
    assert payload["main_flow_feedback"] == {
        "status": "helpful",
        "supported_document_type": "funding_proof",
        "current_focus_document_type": "funding_proof",
        "message": (
            "这份材料已加入案例证据，候选证明点为 funding_proof。"
            "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
        ),
    }
    assert payload["document_assessment"]["main_flow_feedback"] == payload[
        "main_flow_feedback"
    ]


def test_upload_file_maps_funding_alias_into_gate_flow(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    session_id = "sess-funding-alias"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        record = db.get(SessionRecord, session_id)
        assert record is not None
        record.gate_status_json = build_initial_gate_status(
            declared_family="f1",
            scenario_key="upload-funding-alias",
            required_documents=["funding_proof"],
        )
        db.add(record)
        db.commit()

    class RelevantExtractionResult:
        fields = [object()]

    monkeypatch.setattr(
        "app.services.file_service.MultimodalExtractionService.extract",
        lambda self, **kwargs: RelevantExtractionResult(),
    )

    response = client.post(
        f"/v1/sessions/{session_id}/files",
        data={"document_type": "bank_statement"},
        files={
            "file": (
                "bank-statement.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
        },
    )

    assert response.status_code == 202
    payload = response.json()
    assert payload["requested_documents"] == []
    assert payload["remaining_required_documents"] == []
    assert payload["gate_progress"]["overall_status"] == "waiting_for_parse"
    assert payload["main_flow_feedback"] == {
        "status": "helpful",
        "supported_document_type": "funding_proof",
        "current_focus_document_type": "funding_proof",
        "message": (
            "这份材料已加入案例证据，候选证明点为 funding_proof。"
            "你可以继续面签对话，系统会在 Case Board 中更新理解结果。"
        ),
    }
    assert payload["document_assessment"]["main_flow_feedback"] == payload["main_flow_feedback"]


def test_get_file_content_returns_stored_bytes(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = "sess-content"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
      db.add(
          DocumentRecord(
              document_id="doc-content-1",
              session_id=session_id,
              filename="passport.png",
              status="parsed",
              raw_bytes=b"png-bytes",
              raw_text="Material understanding text",
              artifact_json={"content_type": "image/png"},
          )
      )
      db.commit()

    response = client.get(f"/v1/sessions/{session_id}/files/doc-content-1/content")

    assert response.status_code == 200
    assert response.content == b"png-bytes"
    assert response.headers["content-type"] == "image/png"


def test_delete_file_tombstones_case_memory_evidence(
    client: TestClient,
    db_session_factory,
) -> None:
    session_id = "sess-delete-file"
    seed_session(db_session_factory, session_id)
    material_result = MaterialUnderstandingResult(
        document_type_candidates=[
            DocumentTypeCandidate(document_type="i20", confidence=0.91)
        ],
        evidence_cards=[
            EvidenceCard(
                evidence_id="ev-delete-i20-school",
                source_type="uploaded_file",
                document_id="doc-delete-1",
                excerpt="School Name: Example University",
                claim_refs=["claim-delete-school"],
                confidence=0.91,
            )
        ],
        extracted_claims=[
            CaseClaim(
                claim_id="claim-delete-school",
                field_path="/education/school_name",
                value="Example University",
                status="documented",
                supporting_evidence_ids=["ev-delete-i20-school"],
                confidence=0.91,
            )
        ],
        confidence=0.91,
    )

    with db_session_factory() as db:
        db.add(
            DocumentRecord(
                document_id="doc-delete-1",
                session_id=session_id,
                filename="i20.png",
                status="parsed",
                raw_bytes=b"image",
                raw_text="",
                artifact_json={
                    "material_understanding_result": material_result.model_dump(
                        mode="json"
                    )
                },
            )
        )
        db.commit()

    response = client.delete(f"/v1/sessions/{session_id}/files/doc-delete-1")

    assert response.status_code == 200
    payload = response.json()
    assert payload["document_status"] == "tombstoned"
    assert payload["case_board"]["claims"] == []
    assert payload["case_board"]["evidence_cards"] == []
    with db_session_factory() as db:
        document = db.get(DocumentRecord, "doc-delete-1")
        assert document is not None
        assert document.status == "tombstoned"
        assert document.artifact_json["case_memory_tombstone"] == {
            "status": "tombstoned",
            "reason": "file_delete_api",
        }


def test_get_file_content_rejects_cross_session_document(
    client: TestClient,
    db_session_factory,
) -> None:
    seed_session(db_session_factory, "sess-owner")
    seed_session(db_session_factory, "sess-other")

    with db_session_factory() as db:
      db.add(
          DocumentRecord(
              document_id="doc-owner-1",
              session_id="sess-owner",
              filename="passport.png",
              status="parsed",
              raw_bytes=b"png-bytes",
              raw_text="Material understanding text",
              artifact_json={"content_type": "image/png"},
          )
      )
      db.commit()

    response = client.get("/v1/sessions/sess-other/files/doc-owner-1/content")

    assert response.status_code == 404


def test_get_file_content_uses_cookie_auth_when_enabled(
    client: TestClient,
    db_session_factory,
    monkeypatch,
) -> None:
    monkeypatch.setattr(settings_module.settings, "app_auth_password", "test-password")
    monkeypatch.setattr(settings_module.settings, "app_auth_cookie_secure", False)
    monkeypatch.setattr(settings_module.settings, "app_auth_csrf_protection", True)
    session_id = "sess-auth-content"
    seed_session(db_session_factory, session_id)

    with db_session_factory() as db:
        db.add(
            DocumentRecord(
                document_id="doc-auth-content",
                session_id=session_id,
                filename="passport.png",
                status="parsed",
                raw_bytes=b"png-bytes",
                raw_text="Material understanding text",
                artifact_json={"content_type": "image/png"},
            )
        )
        db.commit()

    assert (
        client.get(f"/v1/sessions/{session_id}/files/doc-auth-content/content").status_code
        == 401
    )

    login_response = client.post(
        "/v1/auth/login",
        json={"password": "test-password"},
        headers={"Origin": ORIGIN},
    )
    assert login_response.status_code == 200

    response = client.get(f"/v1/sessions/{session_id}/files/doc-auth-content/content")

    assert response.status_code == 200
    assert response.content == b"png-bytes"
