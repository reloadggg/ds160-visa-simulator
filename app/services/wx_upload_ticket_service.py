from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import SessionRecord, WxUploadTicketRecord


DEFAULT_WX_UPLOAD_TICKET_TTL_SECONDS = 300
DEFAULT_WX_UPLOAD_TICKET_MAX_FILES = 5
WX_UPLOAD_TICKET_PREFIX = "wxup"

# Fields safe to return on the public ticket status endpoint (no content paths).
_STATUS_RESULT_KEYS = (
    "document_id",
    "file_name",
    "filename",
    "mime_type",
    "size",
    "uploaded_at",
)


class WxUploadTicketError(ValueError):
    status_code = 400


class WxUploadTicketNotFoundError(WxUploadTicketError):
    status_code = 404


class WxUploadTicketExpiredError(WxUploadTicketError):
    status_code = 410


class WxUploadTicketInactiveError(WxUploadTicketError):
    status_code = 409


class WxUploadTicketLimitExceededError(WxUploadTicketError):
    status_code = 409


class WxUploadTicketSessionError(WxUploadTicketError):
    status_code = 404


@dataclass(frozen=True)
class CreatedWxUploadTicket:
    ticket: str
    record: WxUploadTicketRecord


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def hash_ticket(ticket: str) -> str:
    return hashlib.sha256(ticket.encode()).hexdigest()


def new_ticket() -> str:
    return f"{WX_UPLOAD_TICKET_PREFIX}_{secrets.token_urlsafe(32)}"


def _public_upload_result(entry: dict[str, Any]) -> dict[str, Any]:
    """Strip content URLs / nested upload payloads from ticket status results."""
    public: dict[str, Any] = {}
    for key in _STATUS_RESULT_KEYS:
        if key in entry:
            public[key] = entry[key]
    return public


class WxUploadTicketService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_ticket(
        self,
        *,
        session_id: str,
        access_key_id: str | None,
        max_files: int = DEFAULT_WX_UPLOAD_TICKET_MAX_FILES,
        ttl_seconds: int = DEFAULT_WX_UPLOAD_TICKET_TTL_SECONDS,
        now: datetime | None = None,
    ) -> CreatedWxUploadTicket:
        if self.db.get(SessionRecord, session_id) is None:
            raise WxUploadTicketSessionError("session not found")

        current_time = now or utcnow()
        normalized_max_files = max(1, min(int(max_files), 10))
        ticket = new_ticket()
        record = WxUploadTicketRecord(
            ticket_hash=hash_ticket(ticket),
            session_id=session_id,
            access_key_id=access_key_id,
            created_at=current_time,
            expires_at=current_time + timedelta(seconds=max(1, int(ttl_seconds))),
            max_files=normalized_max_files,
            uploaded_count=0,
            status="active",
            upload_results_json=[],
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return CreatedWxUploadTicket(ticket=ticket, record=record)

    def get_record(self, ticket: str) -> WxUploadTicketRecord | None:
        return self.db.get(WxUploadTicketRecord, hash_ticket(ticket))

    def require_record(self, ticket: str) -> WxUploadTicketRecord:
        record = self.get_record(ticket)
        if record is None:
            raise WxUploadTicketNotFoundError("upload ticket not found")
        return record

    def status_for(self, record: WxUploadTicketRecord, *, now: datetime | None = None) -> str:
        if record.status != "active":
            return record.status
        if record.expires_at <= (now or utcnow()):
            return "expired"
        if record.uploaded_count >= record.max_files:
            return "completed"
        return "active"

    def validate_for_upload(
        self,
        ticket: str,
        *,
        now: datetime | None = None,
    ) -> WxUploadTicketRecord:
        """Validate ticket without reserving a slot (read-only check)."""
        record = self.require_record(ticket)
        status = self.status_for(record, now=now)
        if status == "expired":
            record.status = "expired"
            self.db.add(record)
            self.db.commit()
            raise WxUploadTicketExpiredError("upload ticket expired")
        if status == "completed":
            raise WxUploadTicketLimitExceededError("upload ticket file limit exceeded")
        if status != "active":
            raise WxUploadTicketInactiveError("upload ticket is not active")
        if self.db.get(SessionRecord, record.session_id) is None:
            raise WxUploadTicketSessionError("session not found")
        return record

    def reserve_upload_slot(
        self,
        ticket: str,
        *,
        now: datetime | None = None,
    ) -> WxUploadTicketRecord:
        """Reserve one upload slot under a row lock before FileService.upload.

        Increments ``uploaded_count`` immediately so concurrent max_files races
        cannot both pass validation. Callers must ``finalize_reserved_upload``
        on success or ``release_upload_slot`` on failure (which may also
        tombstone a created document).
        """
        current_time = now or utcnow()
        ticket_hash = hash_ticket(ticket)
        locked = self.db.execute(
            select(WxUploadTicketRecord)
            .where(WxUploadTicketRecord.ticket_hash == ticket_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise WxUploadTicketNotFoundError("upload ticket not found")

        status = self.status_for(locked, now=current_time)
        if status == "expired":
            locked.status = "expired"
            self.db.add(locked)
            self.db.commit()
            raise WxUploadTicketExpiredError("upload ticket expired")
        if status == "completed" or locked.uploaded_count >= locked.max_files:
            raise WxUploadTicketLimitExceededError("upload ticket file limit exceeded")
        if status != "active":
            raise WxUploadTicketInactiveError("upload ticket is not active")
        if self.db.get(SessionRecord, locked.session_id) is None:
            raise WxUploadTicketSessionError("session not found")

        locked.uploaded_count = int(locked.uploaded_count or 0) + 1
        if locked.uploaded_count >= locked.max_files:
            locked.status = "completed"
        self.db.add(locked)
        self.db.commit()
        self.db.refresh(locked)
        return locked

    def release_upload_slot(
        self,
        record: WxUploadTicketRecord,
        *,
        now: datetime | None = None,
    ) -> WxUploadTicketRecord:
        """Roll back a reserved slot when FileService.upload fails."""
        del now  # reserved for future TTL-aware release
        locked = self.db.execute(
            select(WxUploadTicketRecord)
            .where(WxUploadTicketRecord.ticket_hash == record.ticket_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise WxUploadTicketNotFoundError("upload ticket not found")

        locked.uploaded_count = max(0, int(locked.uploaded_count or 0) - 1)
        if locked.status == "completed" and locked.uploaded_count < locked.max_files:
            # Only reopen when still within TTL; expired tickets stay expired.
            if locked.expires_at > utcnow():
                locked.status = "active"
        self.db.add(locked)
        self.db.commit()
        self.db.refresh(locked)
        return locked

    def finalize_reserved_upload(
        self,
        record: WxUploadTicketRecord,
        *,
        result_payload: dict[str, Any],
        filename: str | None,
        content_type: str | None,
        size: int | None,
        now: datetime | None = None,
    ) -> WxUploadTicketRecord:
        """Attach result metadata after a successful reserved upload.

        Slot count was already incremented by ``reserve_upload_slot``; this only
        appends the public/debug result entry under lock.
        """
        current_time = now or utcnow()
        locked = self.db.execute(
            select(WxUploadTicketRecord)
            .where(WxUploadTicketRecord.ticket_hash == record.ticket_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise WxUploadTicketNotFoundError("upload ticket not found")

        upload_results = list(locked.upload_results_json or [])
        upload_results.append(
            {
                "document_id": result_payload.get("document_id"),
                "file_name": filename,
                "filename": filename,
                "mime_type": content_type,
                "size": size,
                "uploaded_at": current_time.isoformat(timespec="seconds") + "Z",
                # Full upload payload kept server-side for debugging; status
                # endpoint returns a sanitized view only.
                "upload": result_payload,
            }
        )
        locked.upload_results_json = upload_results
        # Keep count consistent with results when finalize is used after reserve.
        locked.uploaded_count = max(int(locked.uploaded_count or 0), len(upload_results))
        if locked.uploaded_count >= locked.max_files:
            locked.status = "completed"
        self.db.add(locked)
        self.db.commit()
        self.db.refresh(locked)
        return locked

    def record_upload_result(
        self,
        record: WxUploadTicketRecord,
        *,
        result_payload: dict[str, Any],
        filename: str | None,
        content_type: str | None,
        size: int | None,
        now: datetime | None = None,
    ) -> WxUploadTicketRecord:
        """Record an upload under a row lock (legacy path: validate+increment).

        Prefer ``reserve_upload_slot`` before FileService.upload and
        ``finalize_reserved_upload`` after success so max_files cannot race with
        the upload I/O window.
        """
        current_time = now or utcnow()
        locked = self.db.execute(
            select(WxUploadTicketRecord)
            .where(WxUploadTicketRecord.ticket_hash == record.ticket_hash)
            .with_for_update()
        ).scalar_one_or_none()
        if locked is None:
            raise WxUploadTicketNotFoundError("upload ticket not found")

        status = self.status_for(locked, now=current_time)
        if status == "expired":
            locked.status = "expired"
            self.db.add(locked)
            self.db.commit()
            raise WxUploadTicketExpiredError("upload ticket expired")
        if status == "completed" or locked.uploaded_count >= locked.max_files:
            raise WxUploadTicketLimitExceededError("upload ticket file limit exceeded")
        if status != "active":
            raise WxUploadTicketInactiveError("upload ticket is not active")

        upload_results = list(locked.upload_results_json or [])
        upload_results.append(
            {
                "document_id": result_payload.get("document_id"),
                "file_name": filename,
                "filename": filename,
                "mime_type": content_type,
                "size": size,
                "uploaded_at": current_time.isoformat(timespec="seconds") + "Z",
                # Full upload payload kept server-side for debugging; status
                # endpoint returns a sanitized view only.
                "upload": result_payload,
            }
        )
        locked.upload_results_json = upload_results
        locked.uploaded_count = len(upload_results)
        if locked.uploaded_count >= locked.max_files:
            locked.status = "completed"
        self.db.add(locked)
        self.db.commit()
        self.db.refresh(locked)
        return locked

    def status_payload(
        self,
        *,
        ticket: str,
        record: WxUploadTicketRecord,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        status = self.status_for(record, now=now)
        remaining_files = max(0, record.max_files - record.uploaded_count)
        raw_results = list(record.upload_results_json or [])
        return {
            "ticket": ticket,
            "session_id": record.session_id,
            "expires_at": record.expires_at.isoformat(timespec="seconds") + "Z",
            "max_files": record.max_files,
            "uploaded_count": record.uploaded_count,
            "remaining_files": remaining_files,
            "status": status,
            # Public status must not leak content URLs or nested upload paths.
            "upload_results": [
                _public_upload_result(entry)
                for entry in raw_results
                if isinstance(entry, dict)
            ],
        }
