from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.domain.evidence import DocumentChunk, EvidenceItem


class EvidenceRepository:
    def __init__(self, db: Session) -> None:
        self.db = db

    def replace_document_result(
        self,
        document_id: str,
        chunks: list[DocumentChunk],
        evidence_items: list[EvidenceItem],
    ) -> None:
        self.db.execute(
            delete(EvidenceItemRecord).where(EvidenceItemRecord.document_id == document_id)
        )
        self.db.execute(
            delete(DocumentChunkRecord).where(DocumentChunkRecord.document_id == document_id)
        )

        self.db.add_all(
            [
                DocumentChunkRecord(
                    chunk_id=chunk.chunk_id,
                    document_id=chunk.document_id,
                    session_id=chunk.session_id,
                    ordinal=chunk.ordinal,
                    page_number=chunk.page_number,
                    text=chunk.text,
                    metadata_json=chunk.metadata,
                )
                for chunk in chunks
            ]
        )
        self.db.add_all(
            [
                EvidenceItemRecord(
                    evidence_id=item.evidence_id,
                    session_id=item.session_id,
                    document_id=item.document_id,
                    chunk_id=item.chunk_id,
                    evidence_type=item.evidence_type,
                    field_path=item.field_path,
                    value=item.value,
                    excerpt=item.excerpt,
                    confidence=item.confidence,
                    metadata_json=item.metadata,
                )
                for item in evidence_items
            ]
        )

    def list_session_evidence(self, session_id: str) -> list[EvidenceItemRecord]:
        statement = select(EvidenceItemRecord).where(
            EvidenceItemRecord.session_id == session_id
        )
        return list(self.db.scalars(statement).all())

    def list_document_evidence(self, document_id: str) -> list[EvidenceItemRecord]:
        statement = select(EvidenceItemRecord).where(
            EvidenceItemRecord.document_id == document_id
        )
        return list(self.db.scalars(statement).all())
