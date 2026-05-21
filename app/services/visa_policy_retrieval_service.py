from __future__ import annotations

from typing import Any

from app.core.settings import settings
from app.domain.rag import (
    AUTHORITY_WEIGHTS,
    PolicyKnowledgeHit,
    PolicyKnowledgeSearchResult,
)
from app.integrations.siliconflow_embedding_client import SiliconFlowEmbeddingClient
from app.integrations.siliconflow_rerank_client import SiliconFlowRerankClient


DEFAULT_SOURCE_TYPES = [
    "federal_official",
    "post_specific",
    "country_reciprocity",
]


CONTEXT_MATCH_BOOSTS = {
    "post_specific": 1.2,
    "country_reciprocity": 1.6,
}


class VisaPolicyRetrievalService:
    def __init__(
        self,
        *,
        embedding_client: Any | None = None,
        rerank_client: Any | None = None,
        chroma_client: Any | None = None,
    ) -> None:
        self.embedding_client = embedding_client or SiliconFlowEmbeddingClient(
            dimensions=(
                settings.siliconflow_embedding_dimensions
                if settings.siliconflow_embedding_dimensions_supported
                else None
            )
        )
        self.rerank_client = rerank_client or SiliconFlowRerankClient()
        self._chroma_client = chroma_client
        self._active_country: str | None = None
        self._active_post: str | None = None

    def search_policy(
        self,
        query: str,
        *,
        visa_family: str | None = None,
        country: str | None = None,
        post: str | None = None,
        source_types: list[str] | None = None,
        limit: int | None = None,
    ) -> PolicyKnowledgeSearchResult:
        normalized_query = query.strip()
        if not normalized_query:
            return PolicyKnowledgeSearchResult.skipped_result("", "empty_query")

        skip_reason = settings.rag_skip_reason
        if skip_reason is not None:
            return PolicyKnowledgeSearchResult.skipped_result(
                normalized_query,
                skip_reason,
            )

        enabled_source_types = self._enabled_source_types(source_types)
        try:
            self._active_country = self._normalize_metadata_value(country)
            self._active_post = self._normalize_metadata_value(post)
            query_embedding = self.embedding_client.embed([normalized_query])[0]
            candidates = self._vector_candidates(
                query_embedding=query_embedding,
                source_types=enabled_source_types,
                visa_family=visa_family,
                country=country,
                post=post,
            )
            reranked = self._rerank(normalized_query, candidates)
        except Exception:
            return PolicyKnowledgeSearchResult.skipped_result(
                normalized_query,
                "retrieval_error",
            )

        result_limit = limit or settings.rag_rerank_top_n
        hits = [
            hit
            for hit in reranked
            if hit.final_score >= settings.rag_min_final_score
        ][:result_limit]
        return PolicyKnowledgeSearchResult.from_hits(
            query=normalized_query,
            hits=hits,
            max_excerpt_chars=500,
        )

    def _enabled_source_types(self, source_types: list[str] | None) -> list[str]:
        requested = source_types or DEFAULT_SOURCE_TYPES
        enabled = [
            source_type
            for source_type in requested
            if source_type != "third_party_reference"
            or settings.rag_allow_third_party_reference
        ]
        return enabled or DEFAULT_SOURCE_TYPES

    def _vector_candidates(
        self,
        *,
        query_embedding: list[float],
        source_types: list[str],
        visa_family: str | None,
        country: str | None,
        post: str | None,
    ) -> list[PolicyKnowledgeHit]:
        candidates: list[PolicyKnowledgeHit] = []
        for source_type in source_types:
            collection = self._collection(source_type)
            result = collection.query(
                query_embeddings=[query_embedding],
                n_results=settings.rag_vector_top_k_per_collection,
                where=self._where_filter(
                    source_type=source_type,
                    visa_family=visa_family,
                    country=country,
                    post=post,
                ),
                include=["documents", "metadatas", "distances"],
            )
            candidates.extend(self._hits_from_chroma_result(result, source_type))

        candidates.sort(key=lambda hit: (hit.vector_score or 1.0))
        return candidates[: settings.rag_candidate_limit]

    def _rerank(
        self,
        query: str,
        candidates: list[PolicyKnowledgeHit],
    ) -> list[PolicyKnowledgeHit]:
        if not candidates:
            return []
        documents = [candidate.excerpt for candidate in candidates]
        ranking = self.rerank_client.rerank(
            query=query,
            documents=documents,
            top_n=min(settings.rag_rerank_top_n, len(documents)),
        )
        if not ranking:
            for hit in candidates:
                hit.final_score = self._final_score(hit, hit.rerank_score)
            return candidates

        reranked: list[PolicyKnowledgeHit] = []
        for index, score in ranking:
            if index < 0 or index >= len(candidates):
                continue
            hit = candidates[index]
            hit.rerank_score = score
            hit.final_score = self._final_score(hit, score)
            reranked.append(hit)
        reranked.sort(key=lambda hit: (-hit.final_score, hit.source_id, hit.chunk_id))
        return reranked

    def _final_score(self, hit: PolicyKnowledgeHit, rerank_score: float | None) -> float:
        base_score = rerank_score if rerank_score is not None else 0.0
        return min(1.0, base_score * hit.authority_weight * self._context_boost(hit))

    def _context_boost(self, hit: PolicyKnowledgeHit) -> float:
        source_type = hit.source_type
        if source_type == "post_specific" and self._active_post:
            hit_post = self._normalize_metadata_value(hit.metadata.get("post"))
            if hit_post == self._active_post:
                return CONTEXT_MATCH_BOOSTS["post_specific"]
        if source_type == "country_reciprocity" and self._active_country:
            hit_country = self._normalize_metadata_value(hit.metadata.get("country"))
            if hit_country == self._active_country:
                return CONTEXT_MATCH_BOOSTS["country_reciprocity"]
        return 1.0

    def _collection(self, source_type: str) -> Any:
        client = self._chroma_client or self._build_chroma_client()
        return client.get_or_create_collection(self._collection_name(source_type))

    def _build_chroma_client(self) -> Any:
        try:
            import chromadb
        except ImportError as exc:
            raise RuntimeError("chromadb is required for RAG retrieval") from exc

        if settings.rag_chroma_mode == "http":
            return chromadb.HttpClient(
                host=settings.rag_chroma_host,
                port=settings.rag_chroma_port,
                ssl=settings.rag_chroma_ssl,
            )
        return chromadb.PersistentClient(path=settings.rag_chroma_path)

    def _collection_name(self, source_type: str) -> str:
        return f"{settings.rag_collection_prefix}_{source_type}_{settings.rag_index_version}"

    def _where_filter(
        self,
        *,
        source_type: str,
        visa_family: str | None,
        country: str | None,
        post: str | None,
    ) -> dict[str, Any]:
        filters: list[dict[str, Any]] = [{"source_type": source_type}]
        if visa_family:
            filters.append(
                {
                    "visa_family": {
                        "$in": self._metadata_filter_values(visa_family, "all", "")
                    }
                }
            )
        if country:
            filters.append({"country": {"$in": self._metadata_filter_values(country, "")}})
        if post:
            filters.append({"post": {"$in": self._metadata_filter_values(post, "")}})
        if len(filters) == 1:
            return filters[0]
        return {"$and": filters}

    def _metadata_filter_values(self, *values: str | None) -> list[str]:
        normalized_values: list[str] = []
        for value in values:
            if value is None:
                continue
            stripped = value.strip()
            lowered = stripped.lower()
            title_cased = lowered.title()
            for candidate in (stripped, lowered, title_cased):
                if candidate not in normalized_values:
                    normalized_values.append(candidate)
        return normalized_values

    def _normalize_metadata_value(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip().lower()
        return normalized or None

    def _hits_from_chroma_result(
        self,
        result: dict[str, Any],
        fallback_source_type: str,
    ) -> list[PolicyKnowledgeHit]:
        ids = self._first_batch(result.get("ids"))
        documents = self._first_batch(result.get("documents"))
        metadatas = self._first_batch(result.get("metadatas"))
        distances = self._first_batch(result.get("distances"))

        hits: list[PolicyKnowledgeHit] = []
        for index, chunk_id in enumerate(ids):
            metadata = metadatas[index] if index < len(metadatas) else {}
            document = documents[index] if index < len(documents) else ""
            distance = distances[index] if index < len(distances) else None
            source_type = str(metadata.get("source_type") or fallback_source_type)
            authority_weight = float(
                metadata.get(
                    "authority_weight",
                    AUTHORITY_WEIGHTS.get(source_type, 0.5),
                )
            )
            hits.append(
                PolicyKnowledgeHit(
                    chunk_id=str(chunk_id),
                    source_id=str(metadata.get("source_id") or chunk_id),
                    source_type=source_type,
                    title=str(metadata.get("title") or "Untitled policy source"),
                    url=str(metadata.get("url") or ""),
                    section_path=self._string_or_none(metadata.get("section_path")),
                    excerpt=str(document),
                    vector_score=float(distance) if distance is not None else None,
                    authority_weight=authority_weight,
                    fetched_at=self._string_or_none(metadata.get("fetched_at")),
                    metadata=dict(metadata),
                )
            )
        return hits

    def _first_batch(self, value: Any) -> list[Any]:
        if not isinstance(value, list) or not value:
            return []
        first = value[0]
        if isinstance(first, list):
            return first
        return value

    def _string_or_none(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None
