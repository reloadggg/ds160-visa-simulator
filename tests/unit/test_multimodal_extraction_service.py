from __future__ import annotations

from io import BytesIO

import fitz
from PIL import Image
import pytest

from app.domain.evidence import DocumentSourceType
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


def test_extract_image_builds_multimodal_request_and_parses_fields() -> None:
    captured: dict[str, object] = {}

    def fake_invoke(payload: dict) -> dict:
        captured["payload"] = payload
        return {
            "full_text": "Full Name: Ada Lovelace\nPassport Number: P1234567",
            "segments": [
                {
                    "ordinal": 0,
                    "page_number": 1,
                    "text": "Full Name: Ada Lovelace\nPassport Number: P1234567",
                }
            ],
            "fields": [
                {
                    "field_path": "/identity/full_name",
                    "value": "Ada Lovelace",
                    "excerpt": "Full Name: Ada Lovelace",
                    "confidence": 0.98,
                    "page_number": 1,
                },
                {
                    "field_path": "/identity/passport_number",
                    "value": "P1234567",
                    "excerpt": "Passport Number: P1234567",
                    "confidence": 0.97,
                    "page_number": 1,
                },
            ],
        }

    service = MultimodalExtractionService(
        invoke_model=fake_invoke,
        model_name="test-vision-model",
    )

    result = service.extract(
        filename="passport_bio.png",
        raw_bytes=build_png_bytes(),
        source_type=DocumentSourceType.IMAGE,
        document_type="passport_bio",
    )

    assert result is not None
    assert result.parser_name == "multimodal_llm"
    assert result.full_text.startswith("Full Name: Ada Lovelace")
    assert result.fields[0].field_path == "/identity/full_name"
    assert result.fields[1].value == "P1234567"

    payload = captured["payload"]
    assert payload["model"] == "test-vision-model"
    content = payload["messages"][1]["content"]
    assert content[0]["type"] == "text"
    assert any(part["type"] == "image_url" for part in content)


def test_extract_pdf_renders_pages_as_images_for_multimodal_request() -> None:
    captured: dict[str, object] = {}

    def fake_invoke(payload: dict) -> dict:
        captured["payload"] = payload
        return {
            "full_text": "Page 1 text\nPage 2 text",
            "segments": [
                {"ordinal": 0, "page_number": 1, "text": "Page 1 text"},
                {"ordinal": 1, "page_number": 2, "text": "Page 2 text"},
            ],
            "fields": [],
        }

    service = MultimodalExtractionService(invoke_model=fake_invoke)

    result = service.extract(
        filename="ds160.pdf",
        raw_bytes=build_pdf_bytes("Page 1 text", "Page 2 text"),
        source_type=DocumentSourceType.PDF,
        document_type="ds160",
    )

    assert result is not None
    assert len(result.segments) == 2
    assert result.segments[1].page_number == 2

    content = captured["payload"]["messages"][1]["content"]
    image_parts = [part for part in content if part["type"] == "image_url"]
    assert len(image_parts) == 2


def test_extract_returns_none_for_unsupported_document_type() -> None:
    service = MultimodalExtractionService(
        invoke_model=lambda payload: {
            "full_text": "should not be called",
            "segments": [],
            "fields": [],
        }
    )

    result = service.extract(
        filename="bank_statement.png",
        raw_bytes=build_png_bytes(),
        source_type=DocumentSourceType.IMAGE,
        document_type="bank_statement",
    )

    assert result is None


def test_assessment_does_not_infer_document_type_from_filename_when_model_unavailable() -> None:
    service = MultimodalExtractionService()

    assessment = service.assess_document(
        filename="funding_proof_bank_statement.png",
        raw_bytes=build_png_bytes(),
        source_type=DocumentSourceType.IMAGE,
    )

    assert assessment.document_type_candidates == []
    assert assessment.supported_claims == []
    assert assessment.relevance == "unknown"


def test_service_auto_enables_when_model_credentials_are_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.delenv("MULTIMODAL_EXTRACTION_ENABLED", raising=False)

    service = MultimodalExtractionService()

    assert service.enabled is True


def test_service_allows_explicit_disable_even_when_model_credentials_exist(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    monkeypatch.setenv("MULTIMODAL_EXTRACTION_ENABLED", "false")

    service = MultimodalExtractionService()

    assert service.enabled is False
