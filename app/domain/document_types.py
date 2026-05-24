from __future__ import annotations

DOCUMENT_TYPE_ALIASES: dict[str, str] = {
    "ds-160": "ds160",
    "ds 160": "ds160",
    "ds160": "ds160",
    "ds-160 confirmation": "ds160",
    "ds 160 confirmation": "ds160",
    "ds160 confirmation": "ds160",
    "ds-160 confirmation page": "ds160",
    "ds 160 confirmation page": "ds160",
    "ds160 confirmation page": "ds160",
    "passport": "passport_bio",
    "passport bio": "passport_bio",
    "passport bio page": "passport_bio",
    "passport biographic page": "passport_bio",
    "passport_bio": "passport_bio",
    "i-20": "i20",
    "i 20": "i20",
    "i20": "i20",
    "form i-20": "i20",
    "form i20": "i20",
    "bank_statement": "funding_proof",
    "bank statement": "funding_proof",
    "financial_statement": "funding_proof",
    "financial statement": "funding_proof",
    "funding_proof": "funding_proof",
    "funding proof": "funding_proof",
    "financial evidence": "funding_proof",
    "sponsor_letter": "funding_proof",
    "sponsor letter": "funding_proof",
    "affidavit_of_support": "funding_proof",
    "affidavit of support": "funding_proof",
    "scholarship_letter": "funding_proof",
    "scholarship letter": "funding_proof",
    "birth_certificate": "relationship_proof_between_applicant_and_sponsors",
    "birth certificate": "relationship_proof_between_applicant_and_sponsors",
    "household_register": "relationship_proof_between_applicant_and_sponsors",
    "household register": "relationship_proof_between_applicant_and_sponsors",
    "hukou": "relationship_proof_between_applicant_and_sponsors",
    "family_register": "relationship_proof_between_applicant_and_sponsors",
    "family register": "relationship_proof_between_applicant_and_sponsors",
    "relationship_proof": "relationship_proof_between_applicant_and_sponsors",
    "relationship proof": "relationship_proof_between_applicant_and_sponsors",
    "relationship proof to sponsor": "relationship_proof_between_applicant_and_sponsors",
    "relationship proof to sponsor if parent-sponsored": "relationship_proof_between_applicant_and_sponsors",
    "relationship_proof_between_applicant_and_sponsors": "relationship_proof_between_applicant_and_sponsors",
}


def normalize_document_type(document_type: str | None) -> str | None:
    if document_type is None:
        return None
    normalized = document_type.strip().lower()
    if not normalized:
        return None
    return DOCUMENT_TYPE_ALIASES.get(normalized, normalized)
