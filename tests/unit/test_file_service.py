import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import DocumentRecord, JobRecord, SessionRecord
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
