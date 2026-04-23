import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
import fitz

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.domain.evidence import DocumentAssessment
from app.services.file_service import FileService
from app.services.file_service import FileTooLargeError
from app.services.file_service import UnsupportedFileTypeError


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def test_upload_rolls_back_document_when_enqueue_job_fails(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-existing", declared_family="f1"))
            db.commit()

        with testing_session_local() as db:
            service = FileService(db)

            def raise_enqueue_failure(*args, **kwargs):
                raise RuntimeError("queue unavailable")

            monkeypatch.setattr(service.repo, "enqueue_job", raise_enqueue_failure)

            with pytest.raises(RuntimeError, match="queue unavailable"):
                service.upload(
                    "sess-existing",
                    "i20.pdf",
                    build_pdf_bytes("SEVIS ID: N1234567890"),
                    "application/pdf",
                )

        with testing_session_local() as db:
            document_count = db.scalar(select(func.count()).select_from(DocumentRecord))
            job_count = db.scalar(select(func.count()).select_from(JobRecord))

            assert document_count == 0
            assert job_count == 0
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_only_enqueues_job_without_modifying_profile(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-profile.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    profile = ApplicantProfile.minimal("profile-sess-existing")
    profile.funding["primary_source"] = "parents"
    profile.field_states["/funding/primary_source"] = FieldStateRecord(
        state=FieldState.CLAIMED
    )
    profile.field_provenance["/funding/primary_source"] = FieldProvenanceRecord()

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-existing",
                    declared_family="f1",
                    profile_json=profile.model_dump(mode="json"),
                )
            )
            db.commit()

        with testing_session_local() as db:
            result = FileService(db).upload(
                "sess-existing",
                "funding_proof.pdf",
                build_pdf_bytes("Parent sponsor bank statement"),
                "application/pdf",
            )
            document_id = result.document_id
            job_id = result.job_id
            assert document_id.startswith("doc-")
            assert job_id.startswith("job-")

        with testing_session_local() as db:
            session_record = db.get(SessionRecord, "sess-existing")
            document = db.get(DocumentRecord, document_id)
            job = db.get(JobRecord, job_id)

            assert session_record is not None
            refreshed_profile = ApplicantProfile.model_validate(
                session_record.profile_json
            )
            assert (
                refreshed_profile.field_states["/funding/primary_source"].state
                == FieldState.CLAIMED
            )
            assert (
                refreshed_profile.field_provenance["/funding/primary_source"].evidence_refs
                == []
            )

            assert document is not None
            assert document.raw_text == ""
            assert document.artifact_json["status"] == "uploaded"
            assert document.artifact_json["filename"] == "funding_proof.pdf"
            assessment = DocumentAssessment.from_artifact(document.artifact_json)
            assert assessment.document_type == "funding_proof"
            assert assessment.document_type_candidates == ["funding_proof"]
            assert assessment.relevance == "medium"
            assert assessment.supported_claims == ["/funding/primary_source"]
            assert assessment.confidence == 0.65
            assert assessment.feedback_message == (
                "系统识别候选类型：funding_proof。 "
                "支持主张：/funding/primary_source。"
            )
            assert assessment.relevant is True

            assert job is not None
            assert job.payload_json == {"document_id": document_id}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_rejects_file_over_size_limit(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-size.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-existing", declared_family="f1"))
            db.commit()

        monkeypatch.setattr("app.services.file_service.MAX_UPLOAD_SIZE_BYTES", 4)

        with testing_session_local() as db:
            with pytest.raises(FileTooLargeError, match="exceeds 64MB limit"):
                FileService(db).upload("sess-existing", "large.txt", b"12345")
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_rejects_unsupported_file_type(tmp_path) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-type.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-existing", declared_family="f1"))
            db.commit()

        with testing_session_local() as db:
            with pytest.raises(
                UnsupportedFileTypeError,
                match="Only PDF and PNG/JPG/JPEG images are supported",
            ):
                FileService(db).upload(
                    "sess-existing",
                    "notes.txt",
                    b"plain text",
                    "text/plain",
                )
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_returns_feedback_for_irrelevant_document(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-feedback.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    class StubMultimodal:
        def extract(self, **kwargs):
            class Result:
                fields: list = []

            return Result()

    try:
        with testing_session_local() as db:
            db.add(SessionRecord(session_id="sess-existing", declared_family="f1"))
            db.commit()

        with testing_session_local() as db:
            service = FileService(db)
            monkeypatch.setattr(service, "multimodal", StubMultimodal())
            result = service.upload(
                "sess-existing",
                "passport_bio.pdf",
                build_pdf_bytes("Completely unrelated flyer"),
                "application/pdf",
                "passport_bio",
            )

            assert result.relevant is False
            assert "关联较弱" in (result.feedback_message or "")
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_prefers_interviewer_focus_for_main_flow_feedback(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-interviewer-focus.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    class StubMultimodal:
        def extract(self, **kwargs):
            class Result:
                fields = [object()]

            return Result()

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-existing",
                    declared_family="j1",
                    gate_status_json={
                        "declared_family": "j1",
                        "scenario_key": "interviewer-focus-feedback",
                        "status": "pending_documents",
                        "required_documents": [
                            {"document_type": "ds160"},
                            {"document_type": "ds2019"},
                        ],
                    },
                    current_focus_json={
                        "owner": "interviewer_runtime_service",
                        "kind": "required_document",
                        "document_type": "ds2019",
                    },
                    interviewer_state_json={
                        "requested_documents": ["ds2019"],
                    },
                )
            )
            db.commit()

        with testing_session_local() as db:
            service = FileService(db)
            monkeypatch.setattr(service, "multimodal", StubMultimodal())
            result = service.upload(
                "sess-existing",
                "ds2019.pdf",
                build_pdf_bytes("SEVIS sponsor form"),
                "application/pdf",
                "ds2019",
            )

            assert result.main_flow_feedback == {
                "status": "helpful",
                "supported_document_type": "ds2019",
                "current_focus_document_type": "ds2019",
                "message": (
                    "这份材料对当前关键证明 ds2019 有帮助。 "
                    "材料门控层当前最缺的关键证明是 ds160。"
                ),
            }
            assert result.requested_documents == ["ds160"]
            assert result.gate_progress == {
                "overall_status": "waiting_for_parse",
                "ready_count": 0,
                "uploaded_count": 1,
                "missing_count": 1,
                "documents": [
                    {
                        "document_type": "ds160",
                        "status": "missing",
                        "is_uploaded": False,
                        "is_parsed": False,
                        "meets_minimum_fields": False,
                    },
                    {
                        "document_type": "ds2019",
                        "status": "uploaded",
                        "is_uploaded": True,
                        "is_parsed": False,
                        "meets_minimum_fields": False,
                    },
                ],
            }
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_upload_uses_backend_context_text_to_infer_document_type_hint(
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    engine = create_engine(
        f"sqlite:///{tmp_path / 'file-service-context-hint.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)

    class StubMultimodal:
        def extract(self, **kwargs):
            class Result:
                fields = [object()]

            return Result()

    try:
        with testing_session_local() as db:
            db.add(
                SessionRecord(
                    session_id="sess-existing",
                    declared_family="j1",
                    gate_status_json={
                        "declared_family": "j1",
                        "scenario_key": "backend-context-hint",
                        "status": "pending_documents",
                        "required_documents": [
                            {"document_type": "ds160"},
                            {"document_type": "passport_bio"},
                            {"document_type": "ds2019"},
                            {"document_type": "funding_proof"},
                        ],
                    },
                )
            )
            db.commit()

        with testing_session_local() as db:
            service = FileService(db)
            monkeypatch.setattr(service, "multimodal", StubMultimodal())
            result = service.upload(
                "sess-existing",
                "upload.pdf",
                build_pdf_bytes("SEVIS sponsor form"),
                "application/pdf",
                context_text="这是我的 DS-2019 表。",
            )

            assert result.document_type == "ds2019"
            assert result.document_assessment is not None
            assert result.document_assessment.document_type_hint == "ds2019"
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
