from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal, cast

from pydantic import BaseModel, Field, field_validator, model_validator

from app.domain.agent_runtime import (
    CitationRef,
    KnowledgeSourceType,
    PublicClaimType,
    SourceAuthority,
    StalenessPolicy,
)


KnowledgePlaneSourceType = Literal[
    "official_policy",
    "case_evidence",
    "product_rubric",
    "third_party_reference",
]
KnowledgeRetentionScope = Literal[
    "global",
    "tenant_scoped",
    "user_scoped",
    "session_scoped",
]
KnowledgeLifecycleStatus = Literal[
    "active",
    "stale",
    "tombstoned",
    "deleted",
    "invalidated",
]
KnowledgeAuditAction = Literal[
    "ingest_started",
    "ingest_completed",
    "retrieve",
    "cite",
    "tombstone",
    "delete",
    "compact",
    "reembed",
    "invalidate_citation",
]
KnowledgeActorType = Literal["system", "user", "admin", "agent_runtime"]
KnowledgeVectorStore = Literal["pgvector", "chroma", "none"]


POSTGRES_PGVECTOR_BASELINE_TABLES: tuple[str, ...] = (
    "knowledge_sources",
    "knowledge_documents",
    "knowledge_chunks",
    "knowledge_embeddings",
    "citation_claims",
    "ingest_runs",
    "graph_checkpoints",
    "graph_run_events",
    "knowledge_audit_events",
)


def _normalize_unique(values: list[str]) -> list[str]:
    normalized: list[str] = []
    for item in values:
        value = item.strip()
        if value and value not in normalized:
            normalized.append(value)
    return normalized


class KnowledgeScope(BaseModel):
    tenant_id: str | None = None
    user_id: str | None = None
    session_id: str | None = None

    @field_validator("tenant_id", "user_id", "session_id")
    @classmethod
    def normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @property
    def is_session_scoped(self) -> bool:
        return bool(self.tenant_id and self.user_id and self.session_id)

    def storage_filter(self) -> dict[str, str]:
        return {
            key: value
            for key, value in {
                "tenant_id": self.tenant_id,
                "user_id": self.user_id,
                "session_id": self.session_id,
            }.items()
            if value is not None
        }


class KnowledgeSourceManifest(BaseModel):
    source_id: str
    source_type: KnowledgePlaneSourceType
    source_authority: SourceAuthority
    title: str
    uri: str | None = None
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    retention_scope: KnowledgeRetentionScope = "global"
    lifecycle_status: KnowledgeLifecycleStatus = "active"
    synthetic: bool = False
    index_version: str = "v1"

    @field_validator("source_id", "title", "index_version")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge source text fields must not be empty")
        return normalized

    @field_validator("uri")
    @classmethod
    def normalize_uri(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_source_boundaries(self) -> "KnowledgeSourceManifest":
        _validate_source_boundary(
            source_type=self.source_type,
            source_authority=self.source_authority,
            retention_scope=self.retention_scope,
            scope=self.scope,
            synthetic=self.synthetic,
        )
        return self


class KnowledgeDocumentRecord(BaseModel):
    document_id: str
    source_id: str
    source_type: KnowledgePlaneSourceType
    source_authority: SourceAuthority
    title: str
    content_hash: str
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    retention_scope: KnowledgeRetentionScope = "global"
    lifecycle_status: KnowledgeLifecycleStatus = "active"
    synthetic: bool = False
    parser_version: str = "parser.v1"
    index_version: str = "v1"

    @field_validator(
        "document_id",
        "source_id",
        "title",
        "content_hash",
        "parser_version",
        "index_version",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge document text fields must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_document_boundaries(self) -> "KnowledgeDocumentRecord":
        _validate_source_boundary(
            source_type=self.source_type,
            source_authority=self.source_authority,
            retention_scope=self.retention_scope,
            scope=self.scope,
            synthetic=self.synthetic,
        )
        return self


class KnowledgeChunkRecord(BaseModel):
    chunk_id: str
    document_id: str
    source_id: str
    source_type: KnowledgePlaneSourceType
    source_authority: SourceAuthority
    ordinal: int = Field(ge=0)
    span_start: int = Field(ge=0)
    span_end: int = Field(gt=0)
    content_hash: str
    quote_or_summary: str
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    retention_scope: KnowledgeRetentionScope = "global"
    lifecycle_status: KnowledgeLifecycleStatus = "active"
    synthetic: bool = False

    @field_validator(
        "chunk_id",
        "document_id",
        "source_id",
        "content_hash",
        "quote_or_summary",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge chunk text fields must not be empty")
        return normalized

    @model_validator(mode="after")
    def validate_chunk_boundaries(self) -> "KnowledgeChunkRecord":
        if self.span_end <= self.span_start:
            raise ValueError("knowledge chunk span_end must be greater than span_start")
        _validate_source_boundary(
            source_type=self.source_type,
            source_authority=self.source_authority,
            retention_scope=self.retention_scope,
            scope=self.scope,
            synthetic=self.synthetic,
        )
        return self

    def to_citation_ref(
        self,
        *,
        citation_id: str,
        claim_ids: list[str] | None = None,
        retrieved_at: datetime | None = None,
    ) -> CitationRef:
        if self.source_type == "third_party_reference":
            raise ValueError("third_party_reference chunks cannot become public citations")
        if self.lifecycle_status in {"tombstoned", "deleted"}:
            raise ValueError("deleted or tombstoned chunks cannot become public citations")
        return CitationRef(
            citation_id=citation_id,
            source_type=cast(KnowledgeSourceType, self.source_type),
            source_authority=self.source_authority,
            source_id=self.source_id,
            document_id=self.document_id,
            chunk_id=self.chunk_id,
            span_start=self.span_start,
            span_end=self.span_end,
            content_hash=self.content_hash,
            quote_or_summary=self.quote_or_summary,
            retrieved_at=retrieved_at or datetime.now(timezone.utc),
            staleness_policy=_staleness_policy_for_lifecycle(self.lifecycle_status),
            claim_ids=claim_ids or [],
        )


class KnowledgeEmbeddingRecord(BaseModel):
    embedding_id: str
    chunk_id: str
    document_id: str
    source_id: str
    source_type: KnowledgePlaneSourceType
    embedding_model: str
    embedding_model_version: str
    embedding_dimensions: int = Field(gt=0)
    index_version: str
    vector_store: KnowledgeVectorStore = "pgvector"
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    retention_scope: KnowledgeRetentionScope = "global"
    lifecycle_status: KnowledgeLifecycleStatus = "active"
    deletion_request_id: str | None = None

    @field_validator(
        "embedding_id",
        "chunk_id",
        "document_id",
        "source_id",
        "embedding_model",
        "embedding_model_version",
        "index_version",
    )
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge embedding text fields must not be empty")
        return normalized

    @field_validator("deletion_request_id")
    @classmethod
    def normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_embedding_boundaries(self) -> "KnowledgeEmbeddingRecord":
        _validate_case_evidence_scope(
            source_type=self.source_type,
            retention_scope=self.retention_scope,
            scope=self.scope,
        )
        if self.lifecycle_status in {"tombstoned", "deleted"} and not self.deletion_request_id:
            raise ValueError("tombstoned or deleted embeddings require deletion_request_id")
        return self


class KnowledgeRetrievalPlan(BaseModel):
    claim_type: PublicClaimType
    allowed_source_types: list[KnowledgePlaneSourceType]
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    require_citation: bool = False
    top_k_by_source_type: dict[KnowledgePlaneSourceType, int] = Field(default_factory=dict)
    allow_third_party_reference: bool = False

    @field_validator("allowed_source_types")
    @classmethod
    def validate_allowed_source_types(
        cls,
        value: list[KnowledgePlaneSourceType],
    ) -> list[KnowledgePlaneSourceType]:
        normalized: list[KnowledgePlaneSourceType] = []
        for item in value:
            if item not in normalized:
                normalized.append(item)
        if not normalized:
            raise ValueError("retrieval plan requires at least one source type")
        return normalized

    @model_validator(mode="after")
    def validate_claim_source_boundaries(self) -> "KnowledgeRetrievalPlan":
        expected = _source_types_for_claim(self.claim_type)
        if set(self.allowed_source_types) != expected:
            raise ValueError(
                f"{self.claim_type} retrieval must use source types {sorted(expected)}"
            )
        if "third_party_reference" in self.allowed_source_types and not self.allow_third_party_reference:
            raise ValueError("third_party_reference retrieval must be explicitly enabled")
        if "case_evidence" in self.allowed_source_types and not self.scope.is_session_scoped:
            raise ValueError("case evidence retrieval requires tenant, user, and session scope")
        for source_type, top_k in self.top_k_by_source_type.items():
            if source_type not in self.allowed_source_types:
                raise ValueError(f"top_k configured for disallowed source type: {source_type}")
            if top_k <= 0:
                raise ValueError("retrieval top_k values must be positive")
        return self

    @classmethod
    def for_claim_type(
        cls,
        claim_type: PublicClaimType,
        *,
        scope: KnowledgeScope | None = None,
        top_k: int = 4,
    ) -> "KnowledgeRetrievalPlan":
        allowed = sorted(_source_types_for_claim(claim_type))
        return cls(
            claim_type=claim_type,
            allowed_source_types=cast(list[KnowledgePlaneSourceType], allowed),
            scope=scope or KnowledgeScope(),
            require_citation=claim_type in {"official_policy", "case_evidence"},
            top_k_by_source_type={cast(KnowledgePlaneSourceType, item): top_k for item in allowed},
        )


class KnowledgeDeletionPlan(BaseModel):
    deletion_request_id: str
    scope: KnowledgeScope
    document_ids: list[str] = Field(default_factory=list)
    chunk_ids: list[str] = Field(default_factory=list)
    embedding_ids: list[str] = Field(default_factory=list)
    tombstone_first: bool = True
    requires_compaction: bool = True
    reason: str = "session_deleted"

    @field_validator("deletion_request_id", "reason")
    @classmethod
    def validate_non_empty_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("knowledge deletion text fields must not be empty")
        return normalized

    @field_validator("document_ids", "chunk_ids", "embedding_ids")
    @classmethod
    def normalize_ids(cls, value: list[str]) -> list[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def validate_deletion_scope(self) -> "KnowledgeDeletionPlan":
        if not self.scope.is_session_scoped:
            raise ValueError("case evidence deletion requires tenant, user, and session scope")
        return self

    @property
    def storage_filter(self) -> dict[str, str]:
        return self.scope.storage_filter()

    @property
    def affected_tables(self) -> tuple[str, ...]:
        return (
            "knowledge_documents",
            "knowledge_chunks",
            "knowledge_embeddings",
            "citation_claims",
            "knowledge_audit_events",
        )


class KnowledgeAuditEvent(BaseModel):
    audit_id: str
    action: KnowledgeAuditAction
    actor_type: KnowledgeActorType
    occurred_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    scope: KnowledgeScope = Field(default_factory=KnowledgeScope)
    run_id: str | None = None
    source_id: str | None = None
    document_id: str | None = None
    chunk_ids: list[str] = Field(default_factory=list)
    citation_ids: list[str] = Field(default_factory=list)
    deletion_request_id: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("audit_id")
    @classmethod
    def validate_audit_id(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("audit_id must not be empty")
        return normalized

    @field_validator("run_id", "source_id", "document_id", "deletion_request_id")
    @classmethod
    def normalize_optional_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("chunk_ids", "citation_ids")
    @classmethod
    def normalize_ids(cls, value: list[str]) -> list[str]:
        return _normalize_unique(value)

    @model_validator(mode="after")
    def validate_audit_boundaries(self) -> "KnowledgeAuditEvent":
        if self.action in {"retrieve", "cite"} and not self.run_id:
            raise ValueError("retrieval and citation audit events require run_id")
        if self.action == "cite" and not self.citation_ids:
            raise ValueError("citation audit events require citation_ids")
        if self.action in {"tombstone", "delete", "compact"}:
            if not self.deletion_request_id:
                raise ValueError("deletion audit events require deletion_request_id")
            if not self.scope.is_session_scoped:
                raise ValueError("deletion audit events require tenant, user, and session scope")
        return self


class KnowledgePlaneStoragePlan(BaseModel):
    vector_store: KnowledgeVectorStore = "pgvector"
    required_tables: list[str] = Field(
        default_factory=lambda: list(POSTGRES_PGVECTOR_BASELINE_TABLES)
    )

    @field_validator("required_tables")
    @classmethod
    def validate_required_tables(cls, value: list[str]) -> list[str]:
        normalized = _normalize_unique(value)
        missing = set(POSTGRES_PGVECTOR_BASELINE_TABLES) - set(normalized)
        if missing:
            raise ValueError(f"storage plan missing required tables: {sorted(missing)}")
        return normalized

    @property
    def session_delete_tables(self) -> tuple[str, ...]:
        return (
            "knowledge_documents",
            "knowledge_chunks",
            "knowledge_embeddings",
            "citation_claims",
        )


def _source_types_for_claim(claim_type: PublicClaimType) -> set[KnowledgePlaneSourceType]:
    mapping: dict[PublicClaimType, set[KnowledgePlaneSourceType]] = {
        "official_policy": {"official_policy"},
        "case_evidence": {"case_evidence"},
        "product_guidance": {"product_rubric"},
        "conversation_state": {"product_rubric"},
    }
    return mapping[claim_type]


def _validate_source_boundary(
    *,
    source_type: KnowledgePlaneSourceType,
    source_authority: SourceAuthority,
    retention_scope: KnowledgeRetentionScope,
    scope: KnowledgeScope,
    synthetic: bool,
) -> None:
    _validate_case_evidence_scope(
        source_type=source_type,
        retention_scope=retention_scope,
        scope=scope,
    )
    if source_type == "official_policy":
        if source_authority not in {"official", "embassy", "institutional"}:
            raise ValueError("official policy sources require official authority")
        if synthetic:
            raise ValueError("synthetic sources cannot be official_policy")
    if source_type == "product_rubric" and source_authority != "product":
        raise ValueError("product rubric sources require product authority")
    if source_type == "third_party_reference" and source_authority != "third_party_reference":
        raise ValueError("third party reference sources require third_party_reference authority")


def _validate_case_evidence_scope(
    *,
    source_type: KnowledgePlaneSourceType,
    retention_scope: KnowledgeRetentionScope,
    scope: KnowledgeScope,
) -> None:
    if source_type != "case_evidence":
        return
    if retention_scope != "session_scoped":
        raise ValueError("case evidence must be session scoped")
    if not scope.is_session_scoped:
        raise ValueError("case evidence requires tenant, user, and session scope")


def _staleness_policy_for_lifecycle(
    lifecycle_status: KnowledgeLifecycleStatus,
) -> StalenessPolicy:
    if lifecycle_status == "active":
        return "stable"
    if lifecycle_status == "stale":
        return "refresh_required"
    return "invalidated"
