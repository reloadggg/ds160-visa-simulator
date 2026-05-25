from __future__ import annotations

from io import BytesIO

import fitz
from docx import Document

from app.integrations.parsers import extract_text, parse_document


def build_pdf_bytes(*pages: str) -> bytes:
    pdf = fitz.open()
    for text in pages:
        page = pdf.new_page()
        page.insert_text((72, 72), text)
    try:
        return pdf.tobytes()
    finally:
        pdf.close()


def build_docx_bytes(*paragraphs: str) -> bytes:
    buffer = BytesIO()
    document = Document()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    document.save(buffer)
    return buffer.getvalue()


def test_parse_pdf_returns_page_segments() -> None:
    parsed = parse_document(
        "bank.pdf",
        build_pdf_bytes(
            "Parent sponsor bank statement",
            "Account balance: 10000 USD",
        ),
    )

    assert parsed.source_type.value == "pdf"
    assert parsed.parser_name == "pymupdf"
    assert len(parsed.segments) == 2
    assert parsed.segments[0].page_number == 1
    assert parsed.segments[1].page_number == 2
    assert "Parent sponsor bank statement" in parsed.full_text
    assert "Account balance: 10000 USD" in parsed.full_text


def test_parse_text_returns_single_segment() -> None:
    parsed = parse_document("notes.txt", b"Financial proof\nSponsor letter")

    assert parsed.source_type.value == "text"
    assert parsed.parser_name == "plain_text"
    assert len(parsed.segments) == 1
    assert parsed.segments[0].ordinal == 0
    assert parsed.segments[0].page_number is None
    assert parsed.full_text == "Financial proof\nSponsor letter"


def test_parse_docx_returns_paragraph_segments() -> None:
    parsed = parse_document(
        "school_letter.docx",
        build_docx_bytes(
            "University admission letter",
            "",
            "Program: Computer Science",
        ),
    )

    assert parsed.source_type.value == "docx"
    assert parsed.parser_name == "python-docx"
    assert len(parsed.segments) == 2
    assert parsed.segments[0].ordinal == 0
    assert parsed.segments[1].ordinal == 2
    assert parsed.segments[1].text == "Program: Computer Science"


def test_parse_image_does_not_ocr_applicant_material() -> None:
    parsed = parse_document("funding.png", b"not-an-actual-image")

    assert parsed.source_type.value == "image"
    assert parsed.parser_name == "multimodal_required"
    assert parsed.segments == []
    assert parsed.full_text == ""


def test_extract_text_wraps_parse_document_full_text() -> None:
    text = extract_text(
        "cover_letter.md",
        b"Applicant explanation\nFunding comes from parent sponsor",
    )

    assert text == "Applicant explanation\nFunding comes from parent sponsor"
