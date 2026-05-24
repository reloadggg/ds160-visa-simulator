from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from app.domain.agent_runtime import CitationBundle, CitationRef
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


class GraphKnowledgePlaneService:
    """Build graph citation bundles without letting retrieval decide the turn."""

    def __init__(
        self,
        *,
        policy_retrieval: VisaPolicyRetrievalService | None = None,
        max_case_evidence_citations: int = 8,
        max_policy_citations: int = 4,
    ) -> None:
        self.policy_retrieval = policy_retrieval or VisaPolicyRetrievalService()
        self.max_case_evidence_citations = max(max_case_evidence_citations, 0)
        self.max_policy_citations = max(max_policy_citations, 0)

    def build_retrieval_plan(
        self,
        case_state: dict[str, Any],
        *,
        message_text: str,
    ) -> dict[str, Any]:
        session = self._payload(case_state.get("session"))
        visa_family = self._string_or_none(session.get("declared_family"))
        return {
            "schema_version": "graph_retrieval_plan.v1",
            "policy_query": self._policy_query(
                message_text=message_text,
                visa_family=visa_family,
            ),
            "case_evidence": {
                "source": "session_document_chunks",
                "session_id": self._string_or_none(session.get("session_id")),
                "top_k": self.max_case_evidence_citations,
            },
            "product_rubric": {"enabled": False},
        }

    def build_citation_bundle(
        self,
        case_state: dict[str, Any],
        *,
        retrieval_plan: dict[str, Any],
        run_id: str,
    ) -> tuple[CitationBundle, dict[str, Any]]:
        citations: list[CitationRef] = []
        policy_summary = self._policy_citations(retrieval_plan=retrieval_plan)
        citations.extend(policy_summary["citations"])
        case_summary = self._case_evidence_citations(
            case_state,
            run_id=run_id,
        )
        citations.extend(case_summary["citations"])

        bundle = CitationBundle(citations=citations)
        summary = {
            "schema_version": "graph_knowledge_plane.v1",
            "citation_count": len(bundle.citations),
            "official_policy": {
                **policy_summary["summary"],
                "citation_ids": [
                    citation.citation_id
                    for citation in policy_summary["citations"]
                ],
            },
            "case_evidence": {
                **case_summary["summary"],
                "citation_ids": [
                    citation.citation_id for citation in case_summary["citations"]
                ],
            },
            "product_rubric": {"citation_ids": [], "skipped": True},
        }
        return bundle, summary

    def _policy_citations(
        self,
        *,
        retrieval_plan: dict[str, Any],
    ) -> dict[str, Any]:
        query = self._string_or_none(retrieval_plan.get("policy_query"))
        if query is None or self.max_policy_citations <= 0:
            return {
                "citations": [],
                "summary": {
                    "query": query,
                    "hit_count": 0,
                    "skipped": True,
                    "skip_reason": "empty_query",
                },
            }
        result = self.policy_retrieval.search_policy(
            query,
            visa_family=self._string_or_none(retrieval_plan.get("visa_family")),
            limit=self.max_policy_citations,
        )
        citations: list[CitationRef] = []
        for index, hit in enumerate(result.hits[: self.max_policy_citations]):
            if hit.source_type == "third_party_reference":
                continue
            citations.append(
                CitationRef(
                    citation_id=self._citation_id(
                        "policy",
                        hit.source_id,
                        hit.chunk_id,
                        index,
                    ),
                    source_type="official_policy",
                    source_authority=self._policy_authority(hit.source_type),
                    source_id=hit.source_id,
                    document_id=self._string_or_none(hit.metadata.get("document_id"))
                    or hit.source_id,
                    chunk_id=hit.chunk_id,
                    span_start=0,
                    span_end=max(len(hit.excerpt), 1),
                    content_hash=self._content_hash(hit.excerpt),
                    quote_or_summary=hit.excerpt[:500],
                    retrieved_at=datetime.now(timezone.utc),
                    staleness_policy="stable",
                )
            )
        return {
            "citations": citations,
            "summary": {
                "query": result.query,
                "hit_count": result.hit_count,
                "skipped": result.skipped,
                "skip_reason": result.skip_reason,
            },
        }

    def _case_evidence_citations(
        self,
        case_state: dict[str, Any],
        *,
        run_id: str,
    ) -> dict[str, Any]:
        chunks = self._chunks_by_id(case_state.get("document_chunks"))
        citations: list[CitationRef] = []
        skipped_missing_chunks = 0
        for index, evidence in enumerate(self._list_payload(case_state.get("evidence_items"))):
            if len(citations) >= self.max_case_evidence_citations:
                break
            chunk_id = self._string_or_none(evidence.get("chunk_id"))
            document_id = self._string_or_none(evidence.get("document_id"))
            if chunk_id is None or document_id is None:
                skipped_missing_chunks += 1
                continue
            chunk = chunks.get(chunk_id)
            if chunk is None:
                skipped_missing_chunks += 1
                continue
            quote = (
                self._string_or_none(evidence.get("excerpt"))
                or self._string_or_none(chunk.get("text_excerpt"))
                or self._string_or_none(evidence.get("value"))
                or "case evidence"
            )
            citations.append(
                CitationRef(
                    citation_id=self._citation_id(
                        "case",
                        document_id,
                        chunk_id,
                        index,
                    ),
                    source_type="case_evidence",
                    source_authority="user_provided",
                    source_id=case_state.get("session", {}).get("session_id")
                    or run_id,
                    document_id=document_id,
                    chunk_id=chunk_id,
                    span_start=0,
                    span_end=max(len(quote), 1),
                    content_hash=self._content_hash(
                        self._string_or_none(chunk.get("text_excerpt")) or quote
                    ),
                    quote_or_summary=quote[:500],
                    retrieved_at=datetime.now(timezone.utc),
                    staleness_policy="stable",
                    claim_ids=[self._claim_id(evidence, index)],
                )
            )
        return {
            "citations": citations,
            "summary": {
                "candidate_count": len(self._list_payload(case_state.get("evidence_items"))),
                "skipped_missing_chunks": skipped_missing_chunks,
            },
        }

    def _chunks_by_id(self, value: Any) -> dict[str, dict[str, Any]]:
        chunks: dict[str, dict[str, Any]] = {}
        for chunk in self._list_payload(value):
            chunk_id = self._string_or_none(chunk.get("chunk_id"))
            if chunk_id is not None:
                chunks[chunk_id] = chunk
        return chunks

    def _policy_query(
        self,
        *,
        message_text: str,
        visa_family: str | None,
    ) -> str:
        normalized_message = message_text.strip()
        parts = ["DS-160 visa interview"]
        if visa_family:
            parts.append(visa_family.upper())
        if normalized_message:
            parts.append(normalized_message[:200])
        return " ".join(parts)

    def _policy_authority(self, source_type: str) -> str:
        if source_type == "post_specific":
            return "embassy"
        if source_type == "country_reciprocity":
            return "official"
        return "official"

    def _citation_id(self, prefix: str, source_id: str, chunk_id: str, index: int) -> str:
        digest = sha256(f"{source_id}:{chunk_id}:{index}".encode("utf-8")).hexdigest()
        return f"cite-{prefix}-{digest[:12]}"

    def _claim_id(self, evidence: dict[str, Any], index: int) -> str:
        field_path = self._string_or_none(evidence.get("field_path"))
        if field_path:
            slug = field_path.strip("/").replace("/", "-").replace("_", "-")
            return f"claim-{slug}-{index}"
        return f"claim-case-evidence-{index}"

    def _content_hash(self, text: str) -> str:
        return "sha256:" + sha256(text.encode("utf-8")).hexdigest()

    def _payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        return {}

    def _list_payload(self, value: Any) -> list[dict[str, Any]]:
        if not isinstance(value, list):
            return []
        return [dict(item) for item in value if isinstance(item, dict)]

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
