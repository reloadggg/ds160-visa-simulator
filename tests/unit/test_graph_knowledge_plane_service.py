from __future__ import annotations

from app.domain.rag import PolicyKnowledgeHit, PolicyKnowledgeSearchResult
from app.services.graph_knowledge_plane_service import GraphKnowledgePlaneService


class StubPolicyRetrieval:
    def __init__(self, result: PolicyKnowledgeSearchResult) -> None:
        self.result = result
        self.queries: list[str] = []

    def search_policy(self, query: str, **kwargs):
        self.queries.append(query)
        return self.result


def _case_state() -> dict:
    return {
        "session": {
            "session_id": "sess-knowledge",
            "declared_family": "f1",
        },
        "document_chunks": [
            {
                "chunk_id": "chunk-i20-school",
                "document_id": "doc-i20",
                "text_excerpt": "School Name: Example University",
            }
        ],
        "evidence_items": [
            {
                "evidence_id": "evi-school",
                "document_id": "doc-i20",
                "chunk_id": "chunk-i20-school",
                "field_path": "/education/school_name",
                "value": "Example University",
                "excerpt": "School Name: Example University",
            }
        ],
    }


def test_graph_knowledge_plane_builds_case_evidence_citation_bundle() -> None:
    service = GraphKnowledgePlaneService(
        policy_retrieval=StubPolicyRetrieval(
            PolicyKnowledgeSearchResult.skipped_result(
                "DS-160 F1",
                "disabled",
            )
        )
    )
    plan = service.build_retrieval_plan(
        _case_state(),
        message_text="I will study computer science.",
    )

    bundle, summary = service.build_citation_bundle(
        _case_state(),
        retrieval_plan=plan,
        run_id="graph-run-knowledge",
    )

    assert plan["policy_query"] == (
        "DS-160 visa interview F1 I will study computer science."
    )
    assert len(bundle.citations) == 1
    citation = bundle.citations[0]
    assert citation.source_type == "case_evidence"
    assert citation.source_authority == "user_provided"
    assert citation.source_id == "sess-knowledge"
    assert citation.document_id == "doc-i20"
    assert citation.chunk_id == "chunk-i20-school"
    assert citation.content_hash.startswith("sha256:")
    assert citation.claim_ids == ["claim-education-school-name-0"]
    assert summary["official_policy"]["skipped"] is True
    assert summary["official_policy"]["skip_reason"] == "disabled"
    assert summary["case_evidence"]["candidate_count"] == 1
    assert summary["case_evidence"]["skipped_missing_chunks"] == 0


def test_graph_knowledge_plane_maps_official_policy_hits_to_citations() -> None:
    hit = PolicyKnowledgeHit(
        chunk_id="chunk-policy-i20",
        source_id="study_in_the_states_i20",
        source_type="federal_official",
        title="Students and the Form I-20",
        url="https://studyinthestates.dhs.gov/students/prepare/students-and-the-form-i-20",
        section_path="Students and the Form I-20",
        excerpt="All F and M students that study in the United States need a Form I-20.",
        final_score=0.9,
        metadata={"document_id": "doc-policy-i20"},
    )
    service = GraphKnowledgePlaneService(
        policy_retrieval=StubPolicyRetrieval(
            PolicyKnowledgeSearchResult.from_hits(query="F-1 I-20", hits=[hit])
        )
    )
    plan = service.build_retrieval_plan(_case_state(), message_text="Do I need I-20?")

    bundle, summary = service.build_citation_bundle(
        _case_state(),
        retrieval_plan=plan,
        run_id="graph-run-policy",
    )

    policy_citations = [
        citation for citation in bundle.citations if citation.source_type == "official_policy"
    ]
    assert len(policy_citations) == 1
    assert policy_citations[0].source_authority == "official"
    assert policy_citations[0].document_id == "doc-policy-i20"
    assert summary["official_policy"]["hit_count"] == 1


def test_graph_knowledge_plane_excludes_third_party_policy_hits() -> None:
    hit = PolicyKnowledgeHit(
        chunk_id="chunk-blog",
        source_id="visa_blog",
        source_type="third_party_reference",
        title="Visa Blog",
        url="https://example.test/blog",
        excerpt="A blog says something.",
        final_score=0.9,
    )
    service = GraphKnowledgePlaneService(
        policy_retrieval=StubPolicyRetrieval(
            PolicyKnowledgeSearchResult.from_hits(query="F-1", hits=[hit])
        )
    )
    plan = service.build_retrieval_plan(_case_state(), message_text="Do I need I-20?")

    bundle, summary = service.build_citation_bundle(
        _case_state(),
        retrieval_plan=plan,
        run_id="graph-run-third-party",
    )

    assert [citation.source_type for citation in bundle.citations] == ["case_evidence"]
    assert summary["official_policy"]["hit_count"] == 1
    assert summary["official_policy"]["citation_ids"] == []


def test_graph_knowledge_plane_skips_case_evidence_without_live_chunk() -> None:
    service = GraphKnowledgePlaneService(
        policy_retrieval=StubPolicyRetrieval(
            PolicyKnowledgeSearchResult.skipped_result("query", "disabled")
        )
    )
    case_state = _case_state()
    case_state["document_chunks"] = []
    plan = service.build_retrieval_plan(case_state, message_text="hello")

    bundle, summary = service.build_citation_bundle(
        case_state,
        retrieval_plan=plan,
        run_id="graph-run-missing-chunk",
    )

    assert bundle.citations == []
    assert summary["case_evidence"]["candidate_count"] == 1
    assert summary["case_evidence"]["skipped_missing_chunks"] == 1
