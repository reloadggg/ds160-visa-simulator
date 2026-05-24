from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.domain.knowledge_plane import (
    KnowledgeAuditEvent,
    KnowledgeChunkRecord,
    KnowledgeDeletionPlan,
    KnowledgeEmbeddingRecord,
    KnowledgeRetrievalPlan,
    KnowledgeScope,
    KnowledgeSourceManifest,
    KnowledgePlaneStoragePlan,
)


def _session_scope() -> KnowledgeScope:
    return KnowledgeScope(
        tenant_id="tenant-1",
        user_id="user-1",
        session_id="sess-1",
    )


def test_case_evidence_source_requires_session_scope() -> None:
    with pytest.raises(ValidationError, match="case evidence must be session scoped"):
        KnowledgeSourceManifest(
            source_id="src-case",
            source_type="case_evidence",
            source_authority="user_provided",
            title="I-20 Upload",
            retention_scope="user_scoped",
            scope=_session_scope(),
        )

    with pytest.raises(ValidationError, match="tenant, user, and session scope"):
        KnowledgeSourceManifest(
            source_id="src-case",
            source_type="case_evidence",
            source_authority="user_provided",
            title="I-20 Upload",
            retention_scope="session_scoped",
            scope=KnowledgeScope(tenant_id="tenant-1", user_id="user-1"),
        )


def test_official_policy_rejects_user_and_synthetic_authority() -> None:
    with pytest.raises(ValidationError, match="official authority"):
        KnowledgeSourceManifest(
            source_id="src-policy",
            source_type="official_policy",
            source_authority="user_provided",
            title="Unofficial Policy Notes",
        )

    with pytest.raises(ValidationError, match="synthetic"):
        KnowledgeSourceManifest(
            source_id="src-policy",
            source_type="official_policy",
            source_authority="official",
            title="Synthetic Policy Notes",
            synthetic=True,
        )


def test_retrieval_planner_keeps_claims_in_their_own_source_plane() -> None:
    policy_plan = KnowledgeRetrievalPlan.for_claim_type("official_policy")
    guidance_plan = KnowledgeRetrievalPlan.for_claim_type("product_guidance")
    case_plan = KnowledgeRetrievalPlan.for_claim_type(
        "case_evidence",
        scope=_session_scope(),
    )

    assert policy_plan.allowed_source_types == ["official_policy"]
    assert policy_plan.require_citation is True
    assert guidance_plan.allowed_source_types == ["product_rubric"]
    assert guidance_plan.require_citation is False
    assert case_plan.allowed_source_types == ["case_evidence"]

    with pytest.raises(ValidationError, match="official_policy retrieval"):
        KnowledgeRetrievalPlan(
            claim_type="official_policy",
            allowed_source_types=["product_rubric"],
        )

    with pytest.raises(ValidationError, match="case evidence retrieval requires"):
        KnowledgeRetrievalPlan.for_claim_type("case_evidence")


def test_chunk_citation_requires_public_source_and_live_lifecycle() -> None:
    chunk = KnowledgeChunkRecord(
        chunk_id="chunk-i20-school",
        document_id="doc-i20",
        source_id="src-session",
        source_type="case_evidence",
        source_authority="user_provided",
        ordinal=0,
        span_start=5,
        span_end=42,
        content_hash="sha256:abc",
        quote_or_summary="I-20 lists Example University.",
        retention_scope="session_scoped",
        scope=_session_scope(),
    )

    citation = chunk.to_citation_ref(
        citation_id="cite-school",
        claim_ids=["claim-school"],
    )

    assert citation.source_type == "case_evidence"
    assert citation.chunk_id == "chunk-i20-school"
    assert citation.content_hash == "sha256:abc"
    assert citation.claim_ids == ["claim-school"]

    third_party_chunk = chunk.model_copy(
        update={
            "source_type": "third_party_reference",
            "source_authority": "third_party_reference",
            "retention_scope": "global",
            "scope": KnowledgeScope(),
        }
    )
    with pytest.raises(ValueError, match="third_party_reference"):
        third_party_chunk.to_citation_ref(citation_id="cite-third-party")

    deleted_chunk = chunk.model_copy(update={"lifecycle_status": "tombstoned"})
    with pytest.raises(ValueError, match="deleted or tombstoned"):
        deleted_chunk.to_citation_ref(citation_id="cite-deleted")


def test_embedding_tombstone_requires_deletion_request() -> None:
    with pytest.raises(ValidationError, match="deletion_request_id"):
        KnowledgeEmbeddingRecord(
            embedding_id="emb-1",
            chunk_id="chunk-1",
            document_id="doc-1",
            source_id="src-1",
            source_type="case_evidence",
            embedding_model="bge-m3",
            embedding_model_version="2026-05",
            embedding_dimensions=1024,
            index_version="v1",
            retention_scope="session_scoped",
            scope=_session_scope(),
            lifecycle_status="tombstoned",
        )


def test_deletion_plan_is_session_scoped_and_targets_case_evidence_tables() -> None:
    plan = KnowledgeDeletionPlan(
        deletion_request_id="delete-sess-1",
        scope=_session_scope(),
        document_ids=["doc-1", "doc-1", ""],
        chunk_ids=["chunk-1"],
        embedding_ids=["emb-1"],
    )

    assert plan.document_ids == ["doc-1"]
    assert plan.storage_filter == {
        "tenant_id": "tenant-1",
        "user_id": "user-1",
        "session_id": "sess-1",
    }
    assert "knowledge_embeddings" in plan.affected_tables
    assert plan.tombstone_first is True
    assert plan.requires_compaction is True

    with pytest.raises(ValidationError, match="tenant, user, and session scope"):
        KnowledgeDeletionPlan(
            deletion_request_id="delete-all",
            scope=KnowledgeScope(tenant_id="tenant-1"),
        )


def test_audit_events_require_run_id_for_retrieval_and_delete_id_for_deletion() -> None:
    with pytest.raises(ValidationError, match="require run_id"):
        KnowledgeAuditEvent(
            audit_id="audit-retrieve",
            action="retrieve",
            actor_type="agent_runtime",
            scope=_session_scope(),
            chunk_ids=["chunk-1"],
        )

    citation_event = KnowledgeAuditEvent(
        audit_id="audit-cite",
        action="cite",
        actor_type="agent_runtime",
        run_id="run-1",
        scope=_session_scope(),
        citation_ids=["cite-1", "cite-1"],
    )

    assert citation_event.citation_ids == ["cite-1"]

    with pytest.raises(ValidationError, match="deletion_request_id"):
        KnowledgeAuditEvent(
            audit_id="audit-delete",
            action="delete",
            actor_type="system",
            scope=_session_scope(),
        )


def test_storage_plan_keeps_postgres_pgvector_baseline_tables_together() -> None:
    plan = KnowledgePlaneStoragePlan()

    assert plan.vector_store == "pgvector"
    assert "graph_checkpoints" in plan.required_tables
    assert "graph_run_events" in plan.required_tables
    assert "knowledge_audit_events" in plan.required_tables

    with pytest.raises(ValidationError, match="missing required tables"):
        KnowledgePlaneStoragePlan(required_tables=["knowledge_sources"])
