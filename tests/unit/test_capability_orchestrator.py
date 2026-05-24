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
                raw_text = (
                    "Applicant TEST APPLICANT "
                    "Parent PARENT SPONSOR A Parent PARENT SPONSOR B"
                )
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


def test_document_review_context_does_not_include_expected_findings() -> None:
    class StubDocumentRepo:
        def list_session_documents(self, session_id):
            class Document:
                document_id = "doc-1"
                filename = "debug_i20.txt"
                status = "parsed"
                raw_text = "Form I-20\nSchool name: Example University\n"
                artifact_json = {
                    "metadata": {
                        "debug_material_bundle": True,
                        "synthetic_bundle_id": "dbg-bundle-test",
                        "debug_bundle_scenario": "school_mismatch_bundle",
                        "expected_findings": [
                            {"kind": "cross_document_conflict"}
                        ],
                        "document_assessment": {
                            "document_type": "i20",
                            "document_type_candidates": ["i20"],
                            "supported_claims": ["/education/school_name"],
                        },
                    }
                }

            return [Document()]

    class StubEvidence:
        def extract_document_fields(self, document_id, document_type):
            return {"/education/school_name": "Example University"}

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

    serialized = str(review_context)
    assert "expected_findings" not in serialized
    assert "cross_document_conflict" not in serialized
    assert "school_mismatch_bundle" not in serialized
    assert "dbg-bundle-test" not in serialized
    assert review_context["documents"][0]["synthetic_metadata"] == {
        "debug_material_bundle": True,
    }


def test_document_review_fallback_detects_material_field_defects() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    review = orchestrator._fallback_document_review_from_context(
        {
            "profile_claims": {"funding": {"primary_source": "self"}},
            "documents": [
                {
                    "document_id": "doc-i20",
                    "document_type": "i20",
                    "extracted_fields": {
                        "/education/school_name": "Example University",
                        "/education/first_year_cost": "68000",
                    },
                    "extracted_fields_by_document_type": {},
                },
                {
                    "document_id": "doc-admission",
                    "document_type": "admission_letter",
                    "extracted_fields": {
                        "/education/school_name": "Alternate Example University",
                    },
                    "extracted_fields_by_document_type": {},
                },
                {
                    "document_id": "doc-funding",
                    "document_type": "funding_proof",
                    "extracted_fields": {
                        "/funding/primary_source": "parents",
                        "/funding/available_funds": "9800",
                    },
                    "extracted_fields_by_document_type": {},
                },
            ],
        }
    )

    assert review is not None
    assert review["review_status"] == "high_risk"
    assert {
        tuple(conflict["field_paths"])
        for conflict in review["cross_document_conflicts"]
    } >= {
        ("/education/school_name",),
        ("/education/first_year_cost", "/funding/available_funds"),
    }
    assert review["claim_conflicts"][0]["field_paths"] == ["/funding/primary_source"]


def test_document_review_fallback_uses_claim_history_after_profile_conflict() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    review = orchestrator._fallback_document_review_from_context(
        {
            "profile_claims": {
                "funding": {},
                "ds160_view": {
                    "field_claim_history": {
                        "/funding/primary_source": [
                            {
                                "value": "self",
                                "content": "I am self-funded.",
                            }
                        ]
                    }
                },
            },
            "documents": [
                {
                    "document_id": "doc-funding",
                    "document_type": "funding_proof",
                    "extracted_fields": {
                        "/funding/primary_source": "parents",
                        "/funding/available_funds": "82000",
                    },
                    "extracted_fields_by_document_type": {},
                },
            ],
        }
    )

    assert review is not None
    assert review["review_status"] == "high_risk"
    assert review["recommended_next_step"] == "high_risk_review"
    assert review["claim_conflicts"][0]["field_paths"] == ["/funding/primary_source"]


def test_document_review_merge_promotes_high_severity_fallback_conflicts() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    merged = orchestrator._merge_document_review_payload(
        {
            "review_status": "reviewed",
            "primary_document": None,
            "remaining_required_documents": [],
            "verified_documents": ["funding_proof"],
            "cross_document_conflicts": [
                {
                    "conflict_type": "document_vs_document",
                    "severity": "high",
                    "summary": "护照号码不一致。",
                    "document_ids": ["doc-ds160", "doc-passport"],
                    "field_paths": ["/identity/passport_number"],
                    "evidence_refs": [],
                }
            ],
            "claim_conflicts": [],
            "unresolved_verification_points": [],
            "suspicious_documents": [],
            "reviewer_summary": "模型认为可继续。",
            "recommended_next_step": "continue_interview",
        },
        None,
    )

    assert merged["review_status"] == "high_risk"
    assert merged["recommended_next_step"] == "high_risk_review"


def test_document_review_merge_does_not_promote_unverified_missing_funding() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    merged = orchestrator._merge_document_review_payload(
        {
            "review_status": "reviewed",
            "primary_document": "funding_proof",
            "remaining_required_documents": [],
            "verified_documents": [],
            "cross_document_conflicts": [
                {
                    "conflict_type": "claim_vs_document",
                    "severity": "high",
                    "summary": (
                        "No funding proof or sponsor information has been "
                        "provided; first-year funding remains unverified."
                    ),
                    "document_ids": [],
                    "field_paths": ["/funding/primary_source"],
                    "evidence_refs": [],
                }
            ],
            "claim_conflicts": [],
            "unresolved_verification_points": ["funding source unverified"],
            "suspicious_documents": [],
            "reviewer_summary": "Funding remains unverified.",
            "recommended_next_step": "continue_interview",
        },
        None,
    )

    assert merged["review_status"] == "needs_clarification"
    assert merged["recommended_next_step"] == "clarify_conflict"


def test_document_review_merge_promotes_anchored_claim_conflict_with_missing_wording() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    merged = orchestrator._merge_document_review_payload(
        {
            "review_status": "reviewed",
            "primary_document": "i20",
            "remaining_required_documents": [],
            "verified_documents": ["i20"],
            "cross_document_conflicts": [],
            "claim_conflicts": [
                {
                    "conflict_type": "claim_vs_document",
                    "severity": "high",
                    "summary": "用户仍缺少与已提交 I-20 一致的学校说明。",
                    "document_ids": ["doc-i20"],
                    "field_paths": ["/education/school_name"],
                    "evidence_refs": ["evi-i20"],
                }
            ],
            "unresolved_verification_points": [],
            "suspicious_documents": [],
            "reviewer_summary": "口头学校说明与 I-20 不一致。",
            "recommended_next_step": "continue_interview",
        },
        None,
    )

    assert merged["review_status"] == "high_risk"
    assert merged["recommended_next_step"] == "high_risk_review"


def test_document_review_merge_promotes_confirmed_funding_shortfall() -> None:
    orchestrator = CapabilityOrchestrator(db=object())

    merged = orchestrator._merge_document_review_payload(
        {
            "review_status": "reviewed",
            "primary_document": "funding_proof",
            "remaining_required_documents": [],
            "verified_documents": ["i20", "funding_proof"],
            "cross_document_conflicts": [
                {
                    "conflict_type": "missing_verification",
                    "severity": "high",
                    "summary": "资金证明金额低于 I-20 第一年度费用。",
                    "document_ids": ["doc-i20", "doc-bank"],
                    "field_paths": [
                        "/education/first_year_cost",
                        "/funding/available_funds",
                    ],
                    "evidence_refs": [],
                }
            ],
            "claim_conflicts": [],
            "unresolved_verification_points": ["funding shortfall"],
            "suspicious_documents": [],
            "reviewer_summary": "已确认资金覆盖不足。",
            "recommended_next_step": "continue_interview",
        },
        None,
    )

    assert merged["review_status"] == "high_risk"
    assert merged["recommended_next_step"] == "high_risk_review"


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
