from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image

from app.domain.evidence import DocumentAssessment, DocumentSourceType, EvidenceItem
from app.services.material_understanding_service import MaterialUnderstandingService
from app.services.multimodal_extraction_service import MultimodalExtractionService


def build_png_bytes() -> bytes:
    image = Image.new("RGB", (320, 120), "white")
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def test_understand_visual_material_allows_unknown_type_with_evidence_cards() -> None:
    captured: dict[str, object] = {}

    def fake_invoke(payload: dict) -> dict:
        captured["payload"] = payload
        return {
            "document_type_candidates": [
                {"document_type": "relationship_proof_between_applicant_and_sponsors", "confidence": 0.62}
            ],
            "evidence_cards": [
                {
                    "evidence_id": "ev-family",
                    "page_number": 1,
                    "excerpt": "Applicant appears as child of the listed sponsor.",
                    "claim_refs": ["claim-family"],
                    "confidence": 0.78,
                }
            ],
            "extracted_claims": [
                {
                    "claim_id": "claim-family",
                    "field_path": "/funding/sponsor_relationship",
                    "value": "parents",
                    "status": "documented",
                    "supporting_evidence_ids": ["ev-family"],
                    "confidence": 0.78,
                }
            ],
            "unknowns": ["Exact English translation is not visible."],
            "confidence": 0.74,
        }

    multimodal = MultimodalExtractionService(invoke_model=fake_invoke)
    service = MaterialUnderstandingService(multimodal_service=multimodal)

    job = service.understand(
        job_id="job-1",
        document_id="doc-family",
        session_id="sess-1",
        filename="unknown-family-proof.png",
        raw_bytes=build_png_bytes(),
        source_type=DocumentSourceType.IMAGE,
        document_assessment=DocumentAssessment(),
    )

    assert job.status == "completed"
    assert job.result is not None
    assert job.result.document_type_candidates[0].document_type == (
        "relationship_proof_between_applicant_and_sponsors"
    )
    assert job.result.evidence_cards[0].document_id == "doc-family"
    assert job.result.extracted_claims[0].field_path == "/funding/sponsor_relationship"
    assert job.result.unknowns == ["Exact English translation is not visible."]

    payload = captured["payload"]
    content = payload["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert "document_type_hint=unknown" in content[0]["text"]
    assert any(item["type"] == "image_url" for item in content)


def test_understand_visual_material_reports_unavailable_without_model() -> None:
    service = MaterialUnderstandingService(
        multimodal_service=MultimodalExtractionService(),
    )

    job = service.understand(
        job_id="job-1",
        document_id="doc-i20",
        session_id="sess-1",
        filename="i20.pdf",
        raw_bytes=build_pdf_bytes("School Name: Example University"),
        source_type=DocumentSourceType.PDF,
        document_assessment=DocumentAssessment(document_type="i20"),
    )

    assert job.status == "failed"
    assert job.error_code == "model_unavailable"
    assert job.result is None


def test_understand_uses_legacy_evidence_when_model_is_unavailable() -> None:
    service = MaterialUnderstandingService(
        multimodal_service=MultimodalExtractionService(),
    )

    job = service.understand(
        job_id="job-legacy",
        document_id="doc-text",
        session_id="sess-1",
        filename="funding_proof.txt",
        raw_bytes=b"Parent sponsor bank statement",
        source_type=DocumentSourceType.TEXT,
        document_assessment=DocumentAssessment(
            document_type="funding_proof",
            document_type_candidates=["funding_proof"],
            confidence=0.82,
        ),
        legacy_evidence_items=[
            EvidenceItem(
                evidence_id="evi-funding",
                session_id="sess-1",
                document_id="doc-text",
                chunk_id="chunk-1",
                evidence_type="funding_proof",
                field_path="/funding/primary_source",
                value="parents",
                excerpt="Parent sponsor bank statement",
                confidence=0.9,
            )
        ],
    )

    assert job.status == "completed"
    assert job.result is not None
    assert job.result.document_type_candidates[0].document_type == "funding_proof"
    assert job.result.evidence_cards[0].evidence_id == "evi-funding"
    assert job.result.extracted_claims[0].status == "documented"
    assert job.result.proof_points[0].status == "supported"
