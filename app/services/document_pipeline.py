from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.evidence import (
    DocumentArtifact,
    DocumentChunk,
    DocumentSourceType,
    EvidenceItem,
)
from app.integrations.parsers import parse_document
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository


class DocumentPipelineService:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)

    def process_document(self, document_id: str) -> dict[str, int]:
        document = self.documents.get_document(document_id)
        if document is None:
            raise LookupError(f"Document not found: {document_id}")

        parsed = parse_document(document.filename, document.raw_bytes)
        artifact_status = self._resolve_status(parsed.source_type)
        artifact = DocumentArtifact(
            document_id=document.document_id,
            session_id=document.session_id,
            filename=document.filename,
            source_type=parsed.source_type,
            parser_name=parsed.parser_name,
            status=artifact_status,
            page_count=len(parsed.segments),
        )

        chunks = [
            DocumentChunk(
                chunk_id=f"chunk-{uuid4().hex[:12]}",
                document_id=document.document_id,
                session_id=document.session_id,
                ordinal=segment.ordinal,
                page_number=segment.page_number,
                text=segment.text.strip(),
                metadata=segment.metadata,
            )
            for segment in parsed.segments
            if segment.text.strip()
        ]
        evidence_items = self._extract_evidence(
            session_id=document.session_id,
            document_id=document.document_id,
            chunks=chunks,
        )

        document.status = artifact_status
        document.raw_text = parsed.full_text
        document.artifact_json = artifact.model_dump(mode="json")

        self.evidence.replace_document_result(document.document_id, chunks, evidence_items)
        self.documents.save_document(document)
        self.db.flush()

        return {
            "chunk_count": len(chunks),
            "evidence_count": len(evidence_items),
        }

    def _resolve_status(self, source_type: DocumentSourceType) -> str:
        if source_type == DocumentSourceType.UNKNOWN:
            return "unsupported"
        return "parsed"

    def _extract_evidence(
        self,
        session_id: str,
        document_id: str,
        chunks: list[DocumentChunk],
    ) -> list[EvidenceItem]:
        evidence_items: list[EvidenceItem] = []
        for chunk in chunks:
            normalized = chunk.text.lower()
            if "bank statement" not in normalized:
                continue
            if "parent" not in normalized and "sponsor" not in normalized:
                continue

            evidence_items.append(
                EvidenceItem(
                    evidence_id=f"evi-{uuid4().hex[:12]}",
                    session_id=session_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    evidence_type="funding_proof",
                    field_path="/funding/primary_source",
                    value="parents",
                    excerpt=chunk.text[:240],
                )
            )
        return evidence_items
