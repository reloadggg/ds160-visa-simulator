from __future__ import annotations

import re

from sqlalchemy import Select, select
from sqlalchemy.orm import Session

from app.agents.schemas import EvidenceHit
from app.db.evidence_models import DocumentChunkRecord, EvidenceItemRecord
from app.db.models import DocumentRecord
from app.domain.evidence import DocumentSourceType

_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_]+")
_CJK_TOKEN_PATTERN = re.compile(r"[\u4e00-\u9fff]")


class RetrievalService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def search_session_evidence(
        self,
        session_id: str,
        query: str,
        *,
        evidence_type: str | None = None,
        field_path: str | None = None,
        limit: int = 5,
    ) -> list[EvidenceHit]:
        statement = self._build_statement(
            session_id=session_id,
            evidence_type=evidence_type,
            field_path=field_path,
        )
        query_tokens = self._tokenize(query)
        if not query_tokens:
            return []

        scored_hits: list[EvidenceHit] = []
        for evidence, chunk, document in self.db.execute(statement):
            score = self._score_hit(query_tokens, evidence, chunk, document)
            if score <= 0:
                continue

            scored_hits.append(
                EvidenceHit(
                    evidence_id=evidence.evidence_id,
                    document_id=evidence.document_id,
                    chunk_id=evidence.chunk_id,
                    evidence_type=evidence.evidence_type,
                    field_path=evidence.field_path,
                    excerpt=evidence.excerpt,
                    filename=document.filename,
                    source_type=self._resolve_source_type(document.artifact_json),
                    score=score,
                )
            )

        scored_hits.sort(key=lambda hit: (-hit.score, hit.evidence_id))
        return scored_hits[: max(limit, 0)]

    def _build_statement(
        self,
        *,
        session_id: str,
        evidence_type: str | None,
        field_path: str | None,
    ) -> Select[tuple[EvidenceItemRecord, DocumentChunkRecord, DocumentRecord]]:
        statement = (
            select(EvidenceItemRecord, DocumentChunkRecord, DocumentRecord)
            .join(
                DocumentChunkRecord,
                DocumentChunkRecord.chunk_id == EvidenceItemRecord.chunk_id,
            )
            .join(
                DocumentRecord,
                DocumentRecord.document_id == EvidenceItemRecord.document_id,
            )
            .where(EvidenceItemRecord.session_id == session_id)
        )
        if evidence_type is not None:
            statement = statement.where(EvidenceItemRecord.evidence_type == evidence_type)
        if field_path is not None:
            statement = statement.where(EvidenceItemRecord.field_path == field_path)
        return statement

    def _score_hit(
        self,
        query_tokens: set[str],
        evidence: EvidenceItemRecord,
        chunk: DocumentChunkRecord,
        document: DocumentRecord,
    ) -> float:
        searchable_tokens = self._tokenize(
            " ".join(
                value
                for value in (
                    evidence.evidence_type,
                    evidence.field_path,
                    evidence.value or "",
                    evidence.excerpt,
                    chunk.text,
                    document.filename,
                )
                if value
            )
        )
        matches = query_tokens.intersection(searchable_tokens)
        if not matches:
            return 0.0
        return float(len(matches))

    def _tokenize(self, text: str) -> set[str]:
        ascii_tokens = {token.lower() for token in _TOKEN_PATTERN.findall(text)}
        cjk_tokens = set(_CJK_TOKEN_PATTERN.findall(text))
        return ascii_tokens | cjk_tokens

    def _resolve_source_type(self, artifact_json: dict | None) -> DocumentSourceType:
        raw_value = (artifact_json or {}).get("source_type", DocumentSourceType.UNKNOWN.value)
        try:
            return DocumentSourceType(raw_value)
        except ValueError:
            return DocumentSourceType.UNKNOWN
