from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class DocumentSourceType(str, Enum):
    TEXT = "text"
    PDF = "pdf"
    DOCX = "docx"
    IMAGE = "image"
    UNKNOWN = "unknown"


class DocumentArtifact(BaseModel):
    document_id: str
    session_id: str
    filename: str
    source_type: DocumentSourceType
    parser_name: str
    status: str
    page_count: int = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentChunk(BaseModel):
    chunk_id: str
    document_id: str
    session_id: str
    ordinal: int
    page_number: int | None = None
    text: str
    metadata: dict[str, Any] = Field(default_factory=dict)


class EvidenceRef(BaseModel):
    evidence_id: str
    document_id: str
    chunk_id: str
    excerpt: str


class EvidenceItem(BaseModel):
    evidence_id: str
    session_id: str
    document_id: str
    chunk_id: str
    evidence_type: str
    field_path: str
    value: str | None = None
    excerpt: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_ref(self) -> EvidenceRef:
        return EvidenceRef(
            evidence_id=self.evidence_id,
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            excerpt=self.excerpt,
        )
