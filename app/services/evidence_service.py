from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agents.schemas import EvidenceExcerpt
from app.db.evidence_models import EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.evidence import DocumentSourceType


@dataclass
class SessionFieldEvidenceSummary:
    field_path: str
    best_value: str | None
    evidence_refs: list[str]
    has_conflict: bool


class EvidenceService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def get_evidence_excerpt(self, evidence_id: str) -> EvidenceExcerpt | None:
        from app.repositories.document_repo import DocumentRepository

        row = self.db.execute(
            select(EvidenceItemRecord, DocumentRecord)
            .join(DocumentRecord, DocumentRecord.document_id == EvidenceItemRecord.document_id)
            .where(EvidenceItemRecord.evidence_id == evidence_id)
        ).first()
        if row is None:
            return None

        evidence, document = row
        # Tombstoned parent documents must not surface excerpts to tools/UI.
        if DocumentRepository.is_document_tombstoned(document):
            return None
        return EvidenceExcerpt(
            evidence_id=evidence.evidence_id,
            document_id=evidence.document_id,
            chunk_id=evidence.chunk_id,
            excerpt=evidence.excerpt,
            filename=document.filename,
            source_type=self._resolve_source_type(document.artifact_json),
        )

    def extract_document_fields(self, document_id: str, schema_name: str) -> dict[str, str]:
        evidence_items = self.db.scalars(
            select(EvidenceItemRecord).where(
                EvidenceItemRecord.document_id == document_id,
                EvidenceItemRecord.evidence_type == schema_name,
            )
        ).all()
        extracted: dict[str, str] = {}
        best_by_field: dict[str, EvidenceItemRecord] = {}
        for item in evidence_items:
            if not item.value:
                continue
            existing = best_by_field.get(item.field_path)
            if existing is None or item.confidence > existing.confidence:
                best_by_field[item.field_path] = item
        for field_path, item in best_by_field.items():
            extracted[field_path.rsplit("/", 1)[-1]] = item.value
        return extracted

    def summarize_session_field_evidence(
        self,
        session_id: str,
    ) -> dict[str, SessionFieldEvidenceSummary]:
        from app.repositories.document_repo import DocumentRepository

        evidence_items = self.db.scalars(
            select(EvidenceItemRecord).where(EvidenceItemRecord.session_id == session_id)
        ).all()
        document_ids = {item.document_id for item in evidence_items if item.document_id}
        tombstoned_document_ids: set[str] = set()
        if document_ids:
            documents = self.db.scalars(
                select(DocumentRecord).where(DocumentRecord.document_id.in_(document_ids))
            )
            for document in documents:
                if DocumentRepository.is_document_tombstoned(document):
                    tombstoned_document_ids.add(document.document_id)

        grouped: dict[str, list[EvidenceItemRecord]] = defaultdict(list)
        for item in evidence_items:
            if not item.value:
                continue
            if item.document_id in tombstoned_document_ids:
                continue
            grouped[item.field_path].append(item)

        summaries: dict[str, SessionFieldEvidenceSummary] = {}
        for field_path, items in grouped.items():
            best_item = max(items, key=lambda item: (item.confidence, item.evidence_id))
            distinct_values = {
                item.value.strip().lower()
                for item in items
                if item.value and item.value.strip()
            }
            summaries[field_path] = SessionFieldEvidenceSummary(
                field_path=field_path,
                best_value=best_item.value,
                evidence_refs=[item.evidence_id for item in items],
                has_conflict=len(distinct_values) > 1,
            )
        return summaries

    def _resolve_source_type(self, artifact_json: dict | None) -> DocumentSourceType:
        raw_value = (artifact_json or {}).get("source_type", DocumentSourceType.UNKNOWN.value)
        try:
            return DocumentSourceType(raw_value)
        except ValueError:
            return DocumentSourceType.UNKNOWN
