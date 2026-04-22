from app.domain.evidence import DocumentAssessment


def test_document_assessment_reads_legacy_upload_artifact_shape() -> None:
    assessment = DocumentAssessment.from_artifact(
        {
            "document_type": "funding_proof",
            "document_type_hint": "bank_statement",
            "document_type_candidates": ["funding_proof"],
            "relevance": "medium",
            "supported_claims": ["/funding/primary_source"],
            "confidence": 0.61,
            "feedback_message": "legacy feedback",
            "relevant": True,
            "counts_toward_gate": False,
            "main_flow_feedback": {
                "status": "not_helpful",
                "supported_document_type": None,
                "current_focus_document_type": "passport_bio",
                "message": "legacy main flow feedback",
            },
        }
    )

    assert assessment.document_type == "funding_proof"
    assert assessment.document_type_hint == "bank_statement"
    assert assessment.document_type_candidates == ["funding_proof"]
    assert assessment.supported_claims == ["/funding/primary_source"]
    assert assessment.counts_toward_gate is False
    assert assessment.main_flow_feedback is not None
    assert assessment.main_flow_feedback.current_focus_document_type == "passport_bio"


def test_document_assessment_prefers_standardized_nested_shape() -> None:
    assessment = DocumentAssessment.from_artifact(
        {
            "document_type": "passport_bio",
            "metadata": {
                "document_type": "passport_bio",
                "document_assessment": {
                    "document_type": "funding_proof",
                    "document_type_candidates": ["funding_proof"],
                    "relevance": "high",
                    "supported_claims": ["/funding/primary_source"],
                    "confidence": 0.9,
                },
            },
        }
    )

    assert assessment.document_type == "funding_proof"
    assert assessment.document_type_candidates == ["funding_proof"]
    assert assessment.relevance == "high"
    assert assessment.confidence == 0.9
