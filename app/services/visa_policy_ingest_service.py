from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.core.settings import settings
from app.domain.rag import AUTHORITY_WEIGHTS, PolicyKnowledgeIngestResult
from app.integrations.parsers import parse_document
from app.integrations.siliconflow_embedding_client import SiliconFlowEmbeddingClient
from app.services.visa_policy_retrieval_service import VisaPolicyRetrievalService


SUPPORTED_POLICY_SOURCE_TYPES = (
    "federal_official",
    "post_specific",
    "country_reciprocity",
    "third_party_reference",
)
SUPPORTED_POLICY_UPLOAD_SUFFIXES = {".txt", ".md", ".pdf", ".docx", ".png", ".jpg", ".jpeg"}


class PolicyKnowledgeUploadTooLargeError(ValueError):
    pass


class UnsupportedPolicyKnowledgeFileError(ValueError):
    pass


class PolicyKnowledgeParseError(ValueError):
    pass


class PolicyKnowledgeIngestService:
    def __init__(
        self,
        *,
        embedding_client: Any | None = None,
        chroma_client: Any | None = None,
    ) -> None:
        self.embedding_client = embedding_client or SiliconFlowEmbeddingClient(
            dimensions=(
                settings.siliconflow_embedding_dimensions
                if settings.siliconflow_embedding_dimensions_supported
                else None
            )
        )
        self._chroma_client = chroma_client

    def ingest_upload(
        self,
        *,
        filename: str,
        raw_bytes: bytes,
        source_type: str = "third_party_reference",
        title: str | None = None,
        url: str | None = None,
        visa_family: str | None = None,
        country: str | None = None,
        post: str | None = None,
        section_path: str | None = None,
    ) -> PolicyKnowledgeIngestResult:
        normalized_source_type = self._normalize_source_type(source_type)
        normalized_title = title.strip() if title and title.strip() else filename
        self._validate_upload(filename, raw_bytes)

        skip_reason = settings.rag_skip_reason
        if skip_reason is not None:
            return PolicyKnowledgeIngestResult(
                status="skipped",
                source_id=self._source_id(filename, raw_bytes),
                source_type=normalized_source_type,
                title=normalized_title,
                collection_name=self._collection_name(normalized_source_type),
                chunk_count=0,
                skipped=True,
                skip_reason=skip_reason,
            )

        try:
            parsed = parse_document(filename, raw_bytes)
        except (UnicodeDecodeError, ValueError, RuntimeError, OSError) as exc:
            raise PolicyKnowledgeParseError(
                "Uploaded policy file could not be parsed"
            ) from exc
        chunks = self._chunk_text(parsed.full_text)
        if not chunks:
            return PolicyKnowledgeIngestResult(
                status="skipped",
                source_id=self._source_id(filename, raw_bytes),
                source_type=normalized_source_type,
                title=normalized_title,
                collection_name=self._collection_name(normalized_source_type),
                chunk_count=0,
                skipped=True,
                skip_reason="empty_document",
            )

        source_id = self._source_id(filename, raw_bytes)
        embeddings = self._embed_chunks(chunks)
        if len(embeddings) != len(chunks):
            return PolicyKnowledgeIngestResult(
                status="skipped",
                source_id=source_id,
                source_type=normalized_source_type,
                title=normalized_title,
                collection_name=self._collection_name(normalized_source_type),
                chunk_count=0,
                skipped=True,
                skip_reason="embedding_count_mismatch",
            )

        collection_name = self._collection_name(normalized_source_type)
        collection = self._collection(normalized_source_type)
        ids = [f"{source_id}:chunk:{index}" for index in range(len(chunks))]
        metadata = {
            "source_id": source_id,
            "source_type": normalized_source_type,
            "title": normalized_title,
            "url": (url or "").strip(),
            "visa_family": self._normalize_metadata_value(visa_family),
            "country": self._normalize_metadata_value(country),
            "post": self._normalize_metadata_value(post),
            "section_path": (section_path or "").strip(),
            "authority_weight": AUTHORITY_WEIGHTS.get(normalized_source_type, 0.5),
            "fetched_at": datetime.now(UTC).date().isoformat(),
            "filename": filename,
            "parser_name": parsed.parser_name,
            "index_version": settings.rag_index_version,
        }
        metadatas = [
            {
                **metadata,
                "chunk_index": index,
            }
            for index in range(len(chunks))
        ]

        collection.upsert(
            ids=ids,
            embeddings=embeddings,
            documents=chunks,
            metadatas=metadatas,
        )
        return PolicyKnowledgeIngestResult(
            status="indexed",
            source_id=source_id,
            source_type=normalized_source_type,
            title=normalized_title,
            collection_name=collection_name,
            chunk_count=len(chunks),
        )

    def status_payload(self) -> dict[str, Any]:
        skip_reason = settings.rag_skip_reason
        payload: dict[str, Any] = {
            "enabled": settings.rag_enabled,
            "ready": skip_reason is None,
            "status": "available" if skip_reason is None else "unavailable",
            "skip_reason": skip_reason,
            "vector_store": settings.rag_vector_store,
            "index_version": settings.rag_index_version,
            "collection_prefix": settings.rag_collection_prefix,
            "chroma_mode": settings.rag_chroma_mode,
            "embedding_model": settings.siliconflow_embedding_model,
            "rerank_model": settings.siliconflow_rerank_model,
            "upload_max_size_mb": settings.rag_upload_max_size_mb,
            "allow_third_party_reference": settings.rag_allow_third_party_reference,
            "collections": [],
        }
        if skip_reason is not None:
            return payload

        try:
            client = self._client()
            collections = []
            for source_type in SUPPORTED_POLICY_SOURCE_TYPES:
                collection_name = self._collection_name(source_type)
                collection = self._get_existing_collection(client, collection_name)
                collections.append(
                    {
                        "name": collection_name,
                        "source_type": source_type,
                        "count": 0 if collection is None else int(collection.count()),
                    }
                )
            payload["collections"] = collections
            if not any(collection["count"] for collection in collections):
                payload["ready"] = False
                payload["status"] = "index_empty"
                payload["skip_reason"] = "index_empty"
        except Exception:
            payload["ready"] = False
            payload["status"] = "unavailable"
            payload["skip_reason"] = "index_unavailable"
        return payload

    def _validate_upload(self, filename: str, raw_bytes: bytes) -> None:
        max_bytes = settings.rag_upload_max_size_mb * 1024 * 1024
        if len(raw_bytes) > max_bytes:
            raise PolicyKnowledgeUploadTooLargeError(
                f"Uploaded policy file exceeds {settings.rag_upload_max_size_mb}MB limit"
            )
        suffix = Path(filename).suffix.lower()
        if suffix not in SUPPORTED_POLICY_UPLOAD_SUFFIXES:
            raise UnsupportedPolicyKnowledgeFileError(
                "Only TXT, Markdown, PDF, DOCX, PNG, JPG, and JPEG files are supported"
            )

    def _chunk_text(self, text: str) -> list[str]:
        normalized = "\n".join(line.strip() for line in text.splitlines()).strip()
        if not normalized:
            return []
        chunk_size = max(settings.rag_chunk_size, 200)
        overlap = min(max(settings.rag_chunk_overlap, 0), chunk_size // 2)
        chunks: list[str] = []
        start = 0
        while start < len(normalized):
            end = min(start + chunk_size, len(normalized))
            chunk = normalized[start:end].strip()
            if chunk:
                chunks.append(chunk)
            if end >= len(normalized):
                break
            start = max(end - overlap, start + 1)
        return chunks

    def _embed_chunks(self, chunks: list[str]) -> list[list[float]]:
        batch_size = max(settings.siliconflow_embedding_batch_size, 1)
        embeddings: list[list[float]] = []
        for start in range(0, len(chunks), batch_size):
            embeddings.extend(
                self.embedding_client.embed(chunks[start : start + batch_size])
            )
        return embeddings

    def _source_id(self, filename: str, raw_bytes: bytes) -> str:
        digest = hashlib.sha256(raw_bytes).hexdigest()[:16]
        stem = Path(filename).stem.lower()
        normalized_stem = "".join(
            character if character.isalnum() else "_"
            for character in stem
        ).strip("_")[:48]
        return f"upload_{normalized_stem or 'policy'}_{digest}"

    def _normalize_source_type(self, source_type: str) -> str:
        normalized = source_type.strip() or "third_party_reference"
        if normalized not in SUPPORTED_POLICY_SOURCE_TYPES:
            raise UnsupportedPolicyKnowledgeFileError(
                f"Unsupported policy source_type: {source_type}"
            )
        return normalized

    def _normalize_metadata_value(self, value: str | None) -> str:
        return (value or "").strip().lower()

    def _collection(self, source_type: str) -> Any:
        return self._client().get_or_create_collection(self._collection_name(source_type))

    def _client(self) -> Any:
        if self._chroma_client is not None:
            return self._chroma_client
        return VisaPolicyRetrievalService()._build_chroma_client()

    def _collection_name(self, source_type: str) -> str:
        return f"{settings.rag_collection_prefix}_{source_type}_{settings.rag_index_version}"

    def _get_existing_collection(self, client: Any, collection_name: str) -> Any | None:
        if not self._collection_exists(client, collection_name):
            return None
        return client.get_collection(collection_name)

    def _collection_exists(self, client: Any, collection_name: str) -> bool:
        return collection_name in {
            self._collection_name_from_chroma_item(collection)
            for collection in client.list_collections()
        }

    def _collection_name_from_chroma_item(self, collection: Any) -> str:
        if isinstance(collection, str):
            return collection
        return str(getattr(collection, "name", ""))
