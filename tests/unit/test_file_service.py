import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
from app.domain.contracts import (
    ApplicantProfile,
    FieldProvenanceRecord,
    FieldState,
    FieldStateRecord,
)
from app.services.file_service import FileService


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
                service.upload("sess-existing", "i20.txt", b"SEVIS ID: N1234567890")

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
            document_id, job_id = FileService(db).upload(
                "sess-existing",
                "bank-proof.txt",
                b"Parent sponsor bank statement",
            )
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
            assert document.artifact_json == {
                "status": "uploaded",
                "filename": "bank-proof.txt",
            }

            assert job is not None
            assert job.payload_json == {"document_id": document_id}
    finally:
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
