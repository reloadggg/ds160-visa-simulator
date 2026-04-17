from uuid import uuid4

from sqlalchemy.orm import Session

from app.db.models import SessionRecord


class SessionRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def create(self, declared_family: str | None) -> SessionRecord:
        record = SessionRecord(
            session_id=f"sess-{uuid4().hex[:12]}",
            declared_family=declared_family,
        )
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record

    def get(self, session_id: str) -> SessionRecord | None:
        return self.db.get(SessionRecord, session_id)

    def save(self, record: SessionRecord) -> SessionRecord:
        self.db.add(record)
        self.db.commit()
        self.db.refresh(record)
        return record
