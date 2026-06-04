from datetime import UTC, datetime

from sqlalchemy import DateTime, JSON, Index, Integer, LargeBinary, String, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def utc_now_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class SessionRecord(Base):
    __tablename__ = "sessions"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    phase_state: Mapped[str] = mapped_column(String(32), default="intake")
    declared_family: Mapped[str | None] = mapped_column(String(32), nullable=True)
    current_governor_decision: Mapped[str] = mapped_column(
        String(32),
        default="need_more_evidence",
    )
    profile_json: Mapped[dict] = mapped_column(JSON, default=dict)
    route_candidates_json: Mapped[list] = mapped_column(JSON, default=list)
    gate_status_json: Mapped[dict] = mapped_column(JSON, default=dict)
    runtime_trace_json: Mapped[list] = mapped_column(JSON, default=list)
    score_history_json: Mapped[list] = mapped_column(JSON, default=list)
    governor_history_json: Mapped[list] = mapped_column(JSON, default=list)
    interviewer_state_json: Mapped[dict] = mapped_column(JSON, default=dict)
    current_focus_json: Mapped[dict] = mapped_column(JSON, default=dict)


class SessionTurnRecord(Base):
    __tablename__ = "session_turns"
    __table_args__ = (
        Index(
            "ux_session_turns_session_id_turn_index",
            "session_id",
            "turn_index",
            unique=True,
        ),
        Index(
            "ux_session_turns_session_id_client_message_id",
            "session_id",
            "client_message_id",
            unique=True,
            sqlite_where=text("client_message_id IS NOT NULL"),
        ),
    )

    turn_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    turn_index: Mapped[int] = mapped_column(Integer)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    role: Mapped[str] = mapped_column(String(32))
    content: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(64))
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    client_message_id: Mapped[str | None] = mapped_column(
        String(128),
        nullable=True,
        index=True,
    )


class DocumentRecord(Base):
    __tablename__ = "documents"

    document_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    filename: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="uploaded")
    artifact_json: Mapped[dict] = mapped_column(JSON, default=dict)
    raw_bytes: Mapped[bytes] = mapped_column(LargeBinary, default=b"")
    raw_text: Mapped[str] = mapped_column(Text, default="")


class JobRecord(Base):
    __tablename__ = "jobs"

    job_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), default="queued")
    payload_json: Mapped[dict] = mapped_column(JSON, default=dict)


class AuthSessionRecord(Base):
    __tablename__ = "auth_sessions"

    session_id_hash: Mapped[str] = mapped_column(String(64), primary_key=True)
    session_kind: Mapped[str] = mapped_column(String(16), default="user", index=True)
    access_key_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=False))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), index=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    user_agent_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ip_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AccessKeyRecord(Base):
    __tablename__ = "access_keys"

    key_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    usage_limit: Mapped[int] = mapped_column(Integer, default=1)
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now_naive)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    created_by_session_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)


class AccessKeySessionRecord(Base):
    __tablename__ = "access_key_sessions"
    __table_args__ = (
        Index("ux_access_key_sessions_session_id", "session_id", unique=True),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key_id: Mapped[str] = mapped_column(String(32), index=True)
    session_id: Mapped[str] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now_naive)


class AdminSettingRecord(Base):
    __tablename__ = "admin_settings"

    setting_key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
    )


class CaseMemorySnapshotRecord(Base):
    __tablename__ = "case_memory_snapshots"

    session_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    snapshot_json: Mapped[dict] = mapped_column(JSON, default=dict)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        default=utc_now_naive,
    )
