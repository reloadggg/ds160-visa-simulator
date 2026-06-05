from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import secrets
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.db.models import AccessKeyRecord, AccessKeySessionRecord

ACCESS_KEY_PREFIX = "ds160"


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _to_utc_naive(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def hash_secret(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def history_namespace_for_key(key_id: str | None) -> str:
    if not key_id:
        return "local-dev"
    return f"key_{key_id}"


@dataclass(frozen=True)
class CreatedAccessKey:
    plaintext_key: str
    record: AccessKeyRecord


@dataclass(frozen=True)
class RevealedAccessKeySecret:
    key_id: str
    key: str | None
    available: bool
    detail: str | None = None


class AccessKeyService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create_key(
        self,
        *,
        label: str = "",
        usage_limit: int = 1,
        expires_at: datetime | None = None,
        enabled: bool = True,
        created_by_session_hash: str | None = None,
    ) -> CreatedAccessKey:
        normalized_limit = max(1, int(usage_limit))
        key_id = secrets.token_urlsafe(6).replace("-", "").replace("_", "")[:10]
        secret = secrets.token_urlsafe(24)
        plaintext_key = f"{ACCESS_KEY_PREFIX}_{key_id}_{secret}"
        record = AccessKeyRecord(
            key_id=key_id,
            key_hash=hash_secret(plaintext_key),
            key_display_value=plaintext_key,
            label=label.strip()[:160],
            usage_limit=normalized_limit,
            usage_count=0,
            expires_at=_to_utc_naive(expires_at),
            revoked_at=None if enabled else utcnow(),
            created_by_session_hash=created_by_session_hash,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return CreatedAccessKey(plaintext_key=plaintext_key, record=record)

    def lookup_login_key(self, plaintext_key: str) -> AccessKeyRecord | None:
        """Return an existing key for login.

        Login itself must not consume quota or require create-session
        eligibility. Quota, expiration, and disabled-state checks happen when
        creating a new backend interview session so exhausted/expired/disabled
        keys can still open their already-bound server history.
        """
        key_hash = hash_secret(plaintext_key.strip())
        return self.db.execute(
            select(AccessKeyRecord).where(AccessKeyRecord.key_hash == key_hash)
        ).scalar_one_or_none()

    def lookup_valid_key(self, plaintext_key: str, *, now: datetime | None = None) -> AccessKeyRecord | None:
        """Backward-compatible strict lookup for callers that need createability."""
        record = self.lookup_login_key(plaintext_key)
        if record is None:
            return None
        current_time = now or utcnow()
        if not self.can_create_session(record, now=current_time):
            return None
        return record

    @staticmethod
    def can_create_session(record: AccessKeyRecord, *, now: datetime | None = None) -> bool:
        current_time = now or utcnow()
        if record.revoked_at is not None:
            return False
        if record.expires_at is not None and record.expires_at <= current_time:
            return False
        return record.usage_count < record.usage_limit

    def consume_session_quota(self, *, key_id: str, session_id: str) -> AccessKeyRecord:
        record = self.db.get(AccessKeyRecord, key_id)
        if record is None:
            raise PermissionError("access key not found")
        current_time = utcnow()
        if record.revoked_at is not None:
            raise PermissionError("access key is revoked")
        if record.expires_at is not None and record.expires_at <= current_time:
            raise PermissionError("access key is expired")
        if record.usage_count >= record.usage_limit:
            raise PermissionError("access key quota exhausted")
        existing = self.db.execute(
            select(AccessKeySessionRecord).where(
                AccessKeySessionRecord.session_id == session_id
            )
        ).scalar_one_or_none()
        if existing is not None:
            return record
        record.usage_count += 1
        record.last_used_at = current_time
        self.db.add(
            AccessKeySessionRecord(
                key_id=key_id,
                session_id=session_id,
                created_at=current_time,
            )
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def session_owned_by_key(self, *, key_id: str, session_id: str) -> bool:
        return self.db.execute(
            select(AccessKeySessionRecord).where(
                AccessKeySessionRecord.key_id == key_id,
                AccessKeySessionRecord.session_id == session_id,
            )
        ).scalar_one_or_none() is not None

    def update_key(
        self,
        *,
        key_id: str,
        label: str | None = None,
        usage_limit: int | None = None,
        expires_at: datetime | None = None,
        expires_at_set: bool = False,
        enabled: bool | None = None,
    ) -> AccessKeyRecord:
        record = self.db.get(AccessKeyRecord, key_id)
        if record is None:
            raise LookupError("access key not found")
        if label is not None:
            record.label = label.strip()[:160]
        if usage_limit is not None:
            record.usage_limit = max(1, int(usage_limit))
        if expires_at_set:
            record.expires_at = _to_utc_naive(expires_at)
        if enabled is not None:
            record.revoked_at = None if enabled else utcnow()
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def list_keys(
        self,
        *,
        q: str | None = None,
        status: str = "all",
        expired: bool | None = None,
    ) -> list[dict[str, Any]]:
        current_time = utcnow()
        statement = select(AccessKeyRecord).order_by(AccessKeyRecord.created_at.desc())
        if status == "enabled":
            statement = statement.where(AccessKeyRecord.revoked_at.is_(None))
        elif status == "disabled":
            statement = statement.where(AccessKeyRecord.revoked_at.is_not(None))

        if expired is True:
            statement = statement.where(
                AccessKeyRecord.expires_at.is_not(None),
                AccessKeyRecord.expires_at <= current_time,
            )
        elif expired is False:
            statement = statement.where(
                or_(
                    AccessKeyRecord.expires_at.is_(None),
                    AccessKeyRecord.expires_at > current_time,
                )
            )

        records = self.db.execute(statement).scalars().all()
        search_text = q.strip().lower() if q else ""
        if search_text:
            records = [
                record
                for record in records
                if self._record_matches_query(record, search_text)
            ]
        return [self.public_payload(record) for record in records]

    @staticmethod
    def public_payload(record: AccessKeyRecord) -> dict[str, Any]:
        return {
            "key_id": record.key_id,
            "label": record.label,
            "masked_key_preview": AccessKeyService.masked_key_preview(record),
            "secret_available": bool(record.key_display_value),
            "usage_limit": record.usage_limit,
            "usage_count": record.usage_count,
            "remaining_uses": max(0, record.usage_limit - record.usage_count),
            "created_at": record.created_at.isoformat() + "Z",
            "expires_at": record.expires_at.isoformat() + "Z" if record.expires_at else None,
            "last_used_at": record.last_used_at.isoformat() + "Z" if record.last_used_at else None,
            "revoked_at": record.revoked_at.isoformat() + "Z" if record.revoked_at else None,
            "enabled": record.revoked_at is None,
            "can_create_session": AccessKeyService.can_create_session(record),
        }

    @staticmethod
    def quota_payload(record: AccessKeyRecord) -> dict[str, Any]:
        """Return normal-user-visible quota metadata without secret material."""
        return {
            "key_id": record.key_id,
            "label": record.label,
            "usage_limit": record.usage_limit,
            "usage_count": record.usage_count,
            "remaining_uses": max(0, record.usage_limit - record.usage_count),
            "can_create_session": AccessKeyService.can_create_session(record),
            "expires_at": record.expires_at.isoformat() + "Z" if record.expires_at else None,
            "revoked": record.revoked_at is not None,
            "revoked_at": record.revoked_at.isoformat() + "Z" if record.revoked_at else None,
        }

    @staticmethod
    def masked_key_preview(record: AccessKeyRecord) -> str:
        return f"{ACCESS_KEY_PREFIX}_{record.key_id}_••••"

    @staticmethod
    def _record_matches_query(record: AccessKeyRecord, search_text: str) -> bool:
        searchable_values = (
            record.key_id,
            record.label or "",
            AccessKeyService.masked_key_preview(record),
            "enabled" if record.revoked_at is None else "disabled",
        )
        return any(search_text in value.lower() for value in searchable_values)

    def reveal_key_secret(self, key_id: str) -> RevealedAccessKeySecret:
        record = self.db.get(AccessKeyRecord, key_id)
        if record is None:
            raise LookupError("access key not found")
        if not record.key_display_value:
            return RevealedAccessKeySecret(
                key_id=record.key_id,
                key=None,
                available=False,
                detail="该访问密钥是在密钥持久化启用前创建的，明文不可找回。",
            )
        return RevealedAccessKeySecret(
            key_id=record.key_id,
            key=record.key_display_value,
            available=True,
        )
