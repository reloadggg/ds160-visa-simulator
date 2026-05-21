from app.agents.schemas import EvidenceHit
from app.domain.contracts import RiskFlag, ScoreState
from app.domain.evidence import DocumentSourceType
from app.domain.rag import PolicyKnowledgeHit, PolicyKnowledgeSearchResult
from app.services.capability_orchestrator import CapabilityOrchestrator


def test_capability_orchestrator_builds_plan_outputs_and_artifacts(monkeypatch) -> None:
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    score.missing_evidence = ["funding_proof"]
    score.risk_flags = [
        RiskFlag(
            code="supporting_evidence_missing",
            severity="medium",
            status="supported",
            evidence_refs=[],
        )
    ]

    monkeypatch.setattr(
        "app.services.retrieval_service.RetrievalService.search_session_evidence",
        lambda self, session_id, query, evidence_type=None, field_path=None, limit=3: [
            EvidenceHit(
                evidence_id="evi-1",
                document_id="doc-1",
                chunk_id="chunk-1",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                excerpt="Parent sponsor bank statement",
                filename="funding-proof.pdf",
                source_type=DocumentSourceType.PDF,
                score=8.5,
            )
        ],
    )
    monkeypatch.setattr(
        "app.services.visa_policy_retrieval_service.VisaPolicyRetrievalService.search_policy",
        lambda self, query, **kwargs: PolicyKnowledgeSearchResult.from_hits(
            query=query,
            hits=[
                PolicyKnowledgeHit(
                    chunk_id="policy-chunk-1",
                    source_id="study_i20",
                    source_type="federal_official",
                    title="Students and the Form I-20",
                    url="https://studyinthestates.dhs.gov/i20",
                    section_path="I-20",
                    excerpt="Students need a Form I-20.",
                    final_score=0.88,
                    fetched_at="2026-05-21",
                )
            ],
        ),
    )

    result = CapabilityOrchestrator(db=object()).orchestrate(
        session_id="sess-1",
        governor_decision="continue_interview",
        latest_user_message="My parents will sponsor me.",
        dynamic_turn_context={
            "focus_thread": {
                "current_key_proof": "funding_proof",
                "current_risk_code": "supporting_evidence_missing",
            },
            "evidence_digest": {
                "current_focus_document_type": "funding_proof",
                "supported_claims": ["/funding/primary_source"],
                "uploaded_document_count": 1,
                "uploaded_documents": [
                    {
                        "document_id": "doc-1",
                        "filename": "funding-proof.pdf",
                        "document_type": "funding_proof",
                        "main_flow_feedback": {
                            "status": "helpful",
                            "current_focus_document_type": "funding_proof",
                        },
                    }
                ],
                "active_main_flow_feedback": {
                    "status": "helpful",
                    "current_focus_document_type": "funding_proof",
                    "message": "这份材料对当前关键证明有帮助。",
                },
            },
            "advisory_context": {
                "risk_codes": ["supporting_evidence_missing"],
                "missing_evidence": ["funding_proof"],
                "risk_level": "medium",
            },
        },
        score=score,
    )

    assert [item["capability_name"] for item in result.capability_plan] == [
        "document_assessment",
        "document_review",
        "evidence_retrieval",
        "policy_knowledge_retrieval",
        "consistency_review",
    ]
    assert all(item["status"] == "completed" for item in result.capability_plan)
    assert set(result.tool_outputs) == {
        "document_assessment",
        "document_review",
        "evidence_retrieval",
        "policy_knowledge_retrieval",
        "consistency_review",
    }
    assert result.tool_outputs["document_assessment"]["uploaded_document_count"] == 1
    assert result.tool_outputs["document_review"]["review_status"] == "reviewed"
    assert result.tool_outputs["document_review"]["primary_document"] == "funding_proof"
    assert result.tool_outputs["evidence_retrieval"]["hit_count"] == 1
    assert result.tool_outputs["policy_knowledge_retrieval"]["hit_count"] == 1
    assert result.tool_outputs["policy_knowledge_retrieval"]["citations"][0]["source_id"] == "study_i20"
    assert result.tool_outputs["consistency_review"]["risk_codes"] == [
        "supporting_evidence_missing"
    ]
    assert [entry.node_name for entry in result.trace_entries] == [
        "decide_capability",
        "resolve_capability",
    ]
    assert result.artifacts == [
        {
            "kind": "capability",
            "capability_name": "document_assessment",
            "status": "completed",
            "current_focus_document_type": "funding_proof",
            "uploaded_document_count": 1,
            "feedback_status": "helpful",
        },
        {
            "kind": "capability",
            "capability_name": "document_review",
            "status": "completed",
            "review_status": "reviewed",
            "primary_document": "funding_proof",
            "remaining_required_count": 0,
            "conflict_count": 0,
        },
        {
            "kind": "capability",
            "capability_name": "evidence_retrieval",
            "status": "completed",
            "query": "funding proof",
            "hit_count": 1,
        },
        {
            "kind": "capability",
            "capability_name": "policy_knowledge_retrieval",
            "status": "completed",
            "query": "funding proof",
            "hit_count": 1,
            "skip_reason": None,
            "policy_citations": [
                {
                    "source_id": "study_i20",
                    "title": "Students and the Form I-20",
                    "url": "https://studyinthestates.dhs.gov/i20",
                    "section_path": "I-20",
                    "source_type": "federal_official",
                    "fetched_at": "2026-05-21",
                    "excerpt": "Students need a Form I-20.",
                    "final_score": 0.88,
                }
            ],
        },
        {
            "kind": "capability",
            "capability_name": "consistency_review",
            "status": "completed",
            "risk_codes": ["supporting_evidence_missing"],
            "missing_evidence": ["funding_proof"],
        },
    ]


def test_capability_orchestrator_records_policy_retrieval_skip(monkeypatch) -> None:
    score = ScoreState.minimal(profile_version=2, scoring_stage="interview_turn")
    monkeypatch.setattr(
        "app.services.visa_policy_retrieval_service.VisaPolicyRetrievalService.search_policy",
        lambda self, query, **kwargs: PolicyKnowledgeSearchResult.skipped_result(
            query,
            "disabled",
        ),
    )

    result = CapabilityOrchestrator(db=object()).orchestrate(
        session_id="sess-1",
        governor_decision="continue_interview",
        latest_user_message="What is DS-160?",
        dynamic_turn_context={},
        score=score,
    )

    assert result.tool_outputs["policy_knowledge_retrieval"]["skipped"] is True
    policy_plan = [
        item
        for item in result.capability_plan
        if item["capability_name"] == "policy_knowledge_retrieval"
    ][0]
    assert policy_plan["status"] == "skipped"
    assert policy_plan["summary"] == "skipped=disabled"


def test_document_review_context_extracts_fields_for_candidate_document_types() -> None:
    class StubDocumentRepo:
        def list_session_documents(self, session_id):
            class Document:
                document_id = "doc-1"
                filename = "hukou.jpg"
                status = "parsed"
                raw_text = "Applicant LI MINGHAO Father LI WEIGUO Mother ZHANG HUI"
                artifact_json = {
                    "metadata": {
                        "document_assessment": {
                            "document_type": "funding_proof",
                            "document_type_candidates": [
                                "funding_proof",
                                "relationship_proof_between_applicant_and_sponsors",
                            ],
                            "supported_claims": ["/family/parent_names"],
                        }
                    }
                }

            return [Document()]

    class StubEvidence:
        def extract_document_fields(self, document_id, document_type):
            return {f"/{document_type}/field": "value"}

    orchestrator = CapabilityOrchestrator(db=object())
    orchestrator.document_repo = StubDocumentRepo()
    orchestrator.evidence = StubEvidence()

    review_context = orchestrator._build_document_review_context(
        session_id="sess-1",
        dynamic_turn_context={"profile_snapshot": {}, "current_focus": {}},
        evidence_digest={"missing_evidence": []},
        focus_thread={},
        advisory_context={},
        gate_progress={},
    )

    document = review_context["documents"][0]
    assert document["extracted_fields_by_document_type"] == {
        "funding_proof": {"/funding_proof/field": "value"},
        "relationship_proof_between_applicant_and_sponsors": {
            "/relationship_proof_between_applicant_and_sponsors/field": "value"
        },
    }
    assert document["extracted_fields"] == {"/funding_proof/field": "value"}


def test_remaining_required_documents_prefers_active_focus_not_in_gate() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    remaining = orchestrator._remaining_required_documents(
        gate_progress={
            "required_documents": [
                {"document_type": "funding_proof", "status": "uploaded"},
            ]
        },
        evidence_digest={
            "current_focus_document_type": "relationship_proof_between_applicant_and_sponsors",
            "remaining_required_documents": [
                "relationship_proof_between_applicant_and_sponsors"
            ],
        },
    )

    assert remaining == ["relationship_proof_between_applicant_and_sponsors"]
