from sqlalchemy import JSON, LargeBinary, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


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
