"""Unit tests for wx upload ticket atomic limits and status sanitization."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import SessionRecord
from app.services.wx_upload_ticket_service import (
    WxUploadTicketLimitExceededError,
    WxUploadTicketService,
)


@pytest.fixture()
def db_session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'wx-ticket-unit.sqlite3'}",
        connect_args={"check_same_thread": False},
    )
    testing_session_local = sessionmaker(bind=engine, autocommit=False, autoflush=False)
    Base.metadata.create_all(bind=engine)
    db = testing_session_local()
    try:
        db.add(SessionRecord(session_id="sess-wx", declared_family="f1"))
        db.commit()
        yield db
    finally:
        db.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()


def test_status_payload_omits_content_urls(db_session) -> None:
    service = WxUploadTicketService(db_session)
    created = service.create_ticket(
        session_id="sess-wx",
        access_key_id=None,
        max_files=2,
    )
    service.record_upload_result(
        created.record,
        result_payload={
            "document_id": "doc-1",
            "content_url": "/v1/sessions/sess-wx/files/doc-1/content",
            "document_status": "uploaded",
        },
        filename="i20.pdf",
        content_type="application/pdf",
        size=12,
        now=datetime(2026, 1, 1, 12, 0, 0),
    )
    refreshed = service.require_record(created.ticket)
    payload = service.status_payload(ticket=created.ticket, record=refreshed)

    assert payload["uploaded_count"] == 1
    assert payload["upload_results"] == [
        {
            "document_id": "doc-1",
            "file_name": "i20.pdf",
            "filename": "i20.pdf",
            "mime_type": "application/pdf",
            "size": 12,
            "uploaded_at": "2026-01-01T12:00:00Z",
        }
    ]
    assert "content_url" not in str(payload["upload_results"])
    assert "upload" not in payload["upload_results"][0]


def test_record_upload_result_enforces_max_files_atomically(db_session) -> None:
    service = WxUploadTicketService(db_session)
    created = service.create_ticket(
        session_id="sess-wx",
        access_key_id=None,
        max_files=1,
    )
    first = service.record_upload_result(
        created.record,
        result_payload={"document_id": "doc-1"},
        filename="a.pdf",
        content_type="application/pdf",
        size=1,
    )
    assert first.uploaded_count == 1
    assert first.status == "completed"

    with pytest.raises(WxUploadTicketLimitExceededError):
        service.record_upload_result(
            first,
            result_payload={"document_id": "doc-2"},
            filename="b.pdf",
            content_type="application/pdf",
            size=1,
            now=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=1),
        )
