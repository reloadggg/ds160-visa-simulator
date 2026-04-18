from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import EvidenceExcerpt
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.evidence import DocumentSourceType


class EvidenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_evidence_excerpt(self, evidence_id: str) -> EvidenceExcerpt | None:
        row = self.db.execute(
            select(EvidenceItemRecord, DocumentRecord)
            .join(DocumentRecord, DocumentRecord.document_id == EvidenceItemRecord.document_id)
            .where(EvidenceItemRecord.evidence_id == evidence_id)
        ).first()
        if row is None:
            return None

        evidence, document = row
        return EvidenceExcerpt(
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            chunk_id=evidence.chunk_id,
            excerpt=evidence.excerpt,
            filename=document.filename,
            source_type=self._resolve_source_type(document.artifact_json),
        )

    def extract_document_fields(self, document_id: str, schema_name: str) -> dict[str, str]:
        if schema_name != "funding_proof":
            return {}

        evidence_items = self.db.scalars(
            select(EvidenceItemRecord).where(
                EvidenceItemRecord.document_id == document_id,
                EvidenceItemRecord.evidence_type == "funding_proof",
                EvidenceItemRecord.field_path == "/funding/primary_source",
            )
        ).all()
        for item in evidence_items:
            if item.value:
                return {"primary_source": item.value}
        return {}

    def _resolve_source_type(self, artifact_json: dict | None) -> DocumentSourceType:
        raw_value = (artifact_json or {}).get("source_type", DocumentSourceType.UNKNOWN.value)
        try:
            return DocumentSourceType(raw_value)
        except ValueError:
            return DocumentSourceType.UNKNOWN
