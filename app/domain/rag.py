from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator


AUTHORITY_WEIGHTS = {
    "federal_official": 1.0,
    "post_specific": 0.9,
    "country_reciprocity": 0.75,
    "third_party_reference": 0.25,
}


class PolicyKnowledgeHit(BaseModel):
    chunk_id: str
    source_id: str
    source_type: str
    title: str
    url: str
    section_path: str | None = None
    excerpt: str
    vector_score: float | None = None
    rerank_score: float | None = None
    authority_weight: float = 1.0
    final_score: float = 0.0
    fetched_at: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("excerpt")
    @classmethod
    def normalize_excerpt(cls, value: str) -> str:
        return value.strip()

    def citation(self, *, max_excerpt_chars: int = 500) -> dict[str, Any]:
        excerpt = self.excerpt[:max_excerpt_chars].strip()
        return {
            "source_id": self.source_id,
            "title": self.title,
            "url": self.url,
            "section_path": self.section_path,
            "source_type": self.source_type,
            "fetched_at": self.fetched_at,
            "excerpt": excerpt,
            "final_score": self.final_score,
        }


class PolicyKnowledgeSearchResult(BaseModel):
    query: str
    hit_count: int = 0
    hits: list[PolicyKnowledgeHit] = Field(default_factory=list)
    citations: list[dict[str, Any]] = Field(default_factory=list)
    citation_policy: str = "official_first"
    skipped: bool = False
    skip_reason: str | None = None

    @classmethod
    def skipped_result(cls, query: str, reason: str) -> "PolicyKnowledgeSearchResult":
        return cls(query=query, skipped=True, skip_reason=reason)

    @classmethod
    def from_hits(
        cls,
        *,
        query: str,
        hits: list[PolicyKnowledgeHit],
        max_excerpt_chars: int = 500,
    ) -> "PolicyKnowledgeSearchResult":
        return cls(
            query=query,
            hit_count=len(hits),
            hits=hits,
            citations=[
                hit.citation(max_excerpt_chars=max_excerpt_chars)
                for hit in hits
            ],
        )

    def tool_payload(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "hit_count": self.hit_count,
            "hits": [hit.model_dump(mode="json") for hit in self.hits],
            "citations": list(self.citations),
            "citation_policy": self.citation_policy,
            "skipped": self.skipped,
            "skip_reason": self.skip_reason,
        }


class PolicyKnowledgeIngestResult(BaseModel):
    status: str
    source_id: str
    source_type: str
    title: str
    collection_name: str
    chunk_count: int
    skipped: bool = False
    skip_reason: str | None = None
