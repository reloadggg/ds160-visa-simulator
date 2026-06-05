import re
from uuid import uuid4

from sqlalchemy.orm import Session

from app.domain.evidence import (
    DocumentAssessment,
    DocumentArtifact,
    DocumentChunk,
    DocumentSourceType,
    EvidenceItem,
)
from app.domain.document_types import normalize_document_type
from app.integrations.parsers import parse_document
from app.repositories.document_repo import DocumentRepository
from app.repositories.evidence_repo import EvidenceRepository
from app.services.multimodal_extraction_service import MultimodalExtractionService

_STRUCTURED_FIELD_PATTERNS: dict[str, list[tuple[str, str]]] = {
    "passport_bio": [
        (r"full name:\s*(.+)", "/identity/full_name"),
        (r"passport number:\s*(.+)", "/identity/passport_number"),
        (r"nationality:\s*(.+)", "/identity/nationality"),
    ],
    "ds160": [
        (r"full name:\s*(.+)", "/identity/full_name"),
        (r"passport number:\s*(.+)", "/identity/passport_number"),
        (r"travel purpose:\s*(.+)", "/visa_intent/travel_purpose"),
    ],
    "i20": [
        (r"sevis id:\s*(.+)", "/education/sevis_id"),
        (r"school name:\s*(.+)", "/education/school_name"),
        (r"program:\s*(.+)", "/education/program_name"),
    ],
    "admission_letter": [
        (r"school name:\s*(.+)", "/education/school_name"),
        (r"program:\s*(.+)", "/education/program_name"),
    ],
    "relationship_proof_between_applicant_and_sponsors": [
        (r"full name:\s*(.+)", "/identity/full_name"),
        (r"applicant:\s*(.+)", "/identity/full_name"),
        (r"father:\s*(.+)", "/family/father_name"),
        (r"mother:\s*(.+)", "/family/mother_name"),
        (r"relationship:\s*(.+)", "/funding/sponsor_relationship"),
        (r"parent names:\s*(.+)", "/family/parent_names"),
    ],
    "ds2019": [
        (r"sevis id:\s*(.+)", "/education/sevis_id"),
        (r"sponsor:\s*(.+)", "/education/sponsor_name"),
        (r"program:\s*(.+)", "/education/program_name"),
    ],
    "school_letter": [
        (r"school name:\s*(.+)", "/education/school_name"),
        (r"program:\s*(.+)", "/education/program_name"),
    ],
    "itinerary_or_trip_purpose": [
        (r"travel purpose:\s*(.+)", "/visa_intent/travel_purpose"),
    ],
}
_FUNDING_DOCUMENT_TYPES = {"funding_proof"}
_FUNDING_KEYWORDS = (
    "bank statement",
    "financial statement",
    "sponsor letter",
    "affidavit of support",
    "scholarship",
    "stipend",
    "assistantship",
    "fellowship",
    "grant",
    "tuition waiver",
)


class DocumentPipelineService:
    def __init__(
        self,
        db: Session,
        *,
        multimodal_service: MultimodalExtractionService | None = None,
    ) -> None:
        self.db = db
        self.documents = DocumentRepository(db)
        self.evidence = EvidenceRepository(db)
        self.multimodal_service = multimodal_service or MultimodalExtractionService()

    def process_document(self, document_id: str) -> dict[str, int]:
        document = self.documents.get_document(document_id)
        if document is None:
            raise LookupError(f"Document not found: {document_id}")

        previous_artifact = dict(document.artifact_json or {})
        upload_assessment = DocumentAssessment.from_artifact(previous_artifact)
        document_type = self._normalize_document_type(upload_assessment.document_type)
        parsed = parse_document(document.filename, document.raw_bytes)
        multimodal_result = self.multimodal_service.extract(
            filename=document.filename,
            raw_bytes=document.raw_bytes,
            source_type=parsed.source_type,
            document_type=document_type,
        )
        if multimodal_result is not None:
            parsed = self._parsed_from_multimodal(multimodal_result)
        artifact_status = self._resolve_status(parsed.source_type)
        upload_assessment = upload_assessment.model_copy(
            update={"document_type": document_type}
        )
        artifact_metadata = {
            "multimodal_used": multimodal_result is not None,
            "document_type": document_type,
            "document_assessment": upload_assessment.to_metadata_payload(),
        }
        for key in (
            "counts_toward_gate",
            "feedback_message",
            "relevant",
            "main_flow_feedback",
        ):
            value = getattr(upload_assessment, key)
            if value is not None:
                if hasattr(value, "model_dump"):
                    value = value.model_dump(mode="json")
                artifact_metadata[key] = value
        artifact = DocumentArtifact(
            document_id=document.document_id,
            session_id=document.session_id,
            filename=document.filename,
            source_type=parsed.source_type,
            parser_name=parsed.parser_name,
            status=artifact_status,
            page_count=len(parsed.segments),
            metadata=artifact_metadata,
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
            filename=document.filename,
            document_type=document_type,
            chunks=chunks,
            multimodal_fields=multimodal_result.fields if multimodal_result else [],
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
        filename: str,
        document_type: str | None,
        chunks: list[DocumentChunk],
        multimodal_fields: list | None = None,
    ) -> list[EvidenceItem]:
        evidence_items: list[EvidenceItem] = []
        if multimodal_fields:
            evidence_items.extend(
                self._multimodal_fields_to_evidence(
                    session_id=session_id,
                    document_id=document_id,
                    chunks=chunks,
                    document_type=document_type,
                    multimodal_fields=multimodal_fields,
                )
            )
            return evidence_items
        for chunk in chunks:
            normalized = chunk.text.lower()
            funding_source = self._extract_funding_source(
                normalized,
                document_type=document_type,
            )
            if funding_source is not None:
                evidence_items.append(
                    EvidenceItem(
                        evidence_id=f"evi-{uuid4().hex[:12]}",
                        session_id=session_id,
                        document_id=document_id,
                        chunk_id=chunk.chunk_id,
                        evidence_type="funding_proof",
                        field_path="/funding/primary_source",
                        value=funding_source,
                        excerpt=chunk.text[:240],
                    )
                )
            if document_type is None:
                continue
            evidence_items.extend(
                self._extract_structured_fields(
                    session_id=session_id,
                    document_id=document_id,
                    chunk=chunk,
                    document_type=document_type,
                )
            )
        return evidence_items

    def _multimodal_fields_to_evidence(
        self,
        *,
        session_id: str,
        document_id: str,
        chunks: list[DocumentChunk],
        document_type: str | None,
        multimodal_fields: list,
    ) -> list[EvidenceItem]:
        if document_type is None:
            return []
        chunk_by_page = {
            chunk.page_number: chunk for chunk in chunks if chunk.page_number is not None
        }
        fallback_chunk_id = chunks[0].chunk_id if chunks else f"chunk-{uuid4().hex[:12]}"
        items: list[EvidenceItem] = []
        for field in multimodal_fields:
            page_number = getattr(field, "page_number", None)
            chunk = chunk_by_page.get(page_number)
            items.append(
                EvidenceItem(
                    evidence_id=f"evi-{uuid4().hex[:12]}",
                    session_id=session_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id if chunk else fallback_chunk_id,
                    evidence_type=document_type,
                    field_path=field.field_path,
                    value=field.value,
                    excerpt=field.excerpt[:240],
                    confidence=field.confidence,
                    metadata={"page_number": page_number},
                )
            )
        return items

    def _extract_structured_fields(
        self,
        *,
        session_id: str,
        document_id: str,
        chunk: DocumentChunk,
        document_type: str,
    ) -> list[EvidenceItem]:
        patterns = _STRUCTURED_FIELD_PATTERNS.get(document_type, [])
        normalized = chunk.text.lower()
        items: list[EvidenceItem] = []
        for pattern, field_path in patterns:
            match = re.search(pattern, normalized)
            if not match:
                continue
            raw_value = match.group(1).strip()
            value = self._slice_original_value(chunk.text, raw_value)
            items.append(
                EvidenceItem(
                    evidence_id=f"evi-{uuid4().hex[:12]}",
                    session_id=session_id,
                    document_id=document_id,
                    chunk_id=chunk.chunk_id,
                    evidence_type=document_type,
                    field_path=field_path,
                    value=value,
                    excerpt=f"{field_path}: {value}"[:240],
                )
            )
        return items

    def _normalize_document_type(self, document_type: str | None) -> str | None:
        return normalize_document_type(document_type)

    def _slice_original_value(self, original_text: str, lowered_value: str) -> str:
        original_lower = original_text.lower()
        start = original_lower.find(lowered_value)
        if start == -1:
            return lowered_value.strip()
        return original_text[start : start + len(lowered_value)].strip()

    def _extract_funding_source(
        self,
        normalized_text: str,
        *,
        document_type: str | None = None,
    ) -> str | None:
        if (
            document_type not in _FUNDING_DOCUMENT_TYPES
            and not any(keyword in normalized_text for keyword in _FUNDING_KEYWORDS)
        ):
            return None

        keyword_groups = (
            ("employer", ("employer", "company", "corporate", "work sponsor")),
            (
                "school",
                ("scholarship", "stipend", "assistantship", "fellowship"),
            ),
            (
                "self",
                (
                    "self-funded",
                    "self funded",
                    "personal savings",
                    "my own",
                    "own funds",
                ),
            ),
            (
                "parents",
                ("parent", "parents", "father", "mother", "mom", "dad"),
            ),
            ("sponsor", ("sponsor",)),
        )
        for source, keywords in keyword_groups:
            if any(keyword in normalized_text for keyword in keywords):
                return source
        return None

    def _parsed_from_multimodal(self, result) -> object:
        from app.integrations.parsers import ParsedDocument, ParsedSegment

        return ParsedDocument(
            source_type=result.source_type,
            parser_name=result.parser_name,
            segments=[
                ParsedSegment(
                    ordinal=segment.ordinal,
                    page_number=segment.page_number,
                    text=segment.text,
                    metadata=segment.metadata,
                )
                for segment in result.segments
            ],
        )
