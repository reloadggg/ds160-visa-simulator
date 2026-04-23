from app.agents.schemas import EvidenceHit
from app.domain.contracts import RiskFlag, ScoreState
from app.domain.evidence import DocumentSourceType
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
        "evidence_retrieval",
        "consistency_review",
    ]
    assert all(item["status"] == "completed" for item in result.capability_plan)
    assert set(result.tool_outputs) == {
        "document_assessment",
        "evidence_retrieval",
        "consistency_review",
    }
    assert result.tool_outputs["document_assessment"]["uploaded_document_count"] == 1
    assert result.tool_outputs["evidence_retrieval"]["hit_count"] == 1
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
            "capability_name": "evidence_retrieval",
            "status": "completed",
            "query": "funding proof",
            "hit_count": 1,
        },
        {
            "kind": "capability",
            "capability_name": "consistency_review",
            "status": "completed",
            "risk_codes": ["supporting_evidence_missing"],
            "missing_evidence": ["funding_proof"],
        },
    ]
