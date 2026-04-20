from __future__ import annotations

DOCUMENT_TYPE_ALIASES: dict[str, str] = {
    "bank_statement": "funding_proof",
    "financial_statement": "funding_proof",
    "funding_proof": "funding_proof",
    "sponsor_letter": "funding_proof",
    "affidavit_of_support": "funding_proof",
    "scholarship_letter": "funding_proof",
}


def normalize_document_type(document_type: str | None) -> str | None:
    if document_type is None:
        return None
    normalized = document_type.strip().lower()
    if not normalized:
        return None
    return DOCUMENT_TYPE_ALIASES.get(normalized, normalized)
