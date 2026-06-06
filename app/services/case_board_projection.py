from __future__ import annotations

from typing import Any


UNRESOLVED_PROOF_STATUSES = {"missing", "partial", "contradicted"}


def case_board_has_state(case_board: dict[str, Any]) -> bool:
    if case_board.get("latest_material") or case_board.get("next_move"):
        return True
    return any(
        _list_payload(case_board.get(key))
        for key in (
            "claims",
            "evidence_cards",
            "proof_points",
            "open_proof_points",
            "conflicts",
        )
    )


def missing_evidence_from_case_board(case_board: dict[str, Any]) -> list[str]:
    missing_evidence: list[str] = []
    for proof in [
        *_list_payload(case_board.get("proof_points")),
        *_list_payload(case_board.get("open_proof_points")),
    ]:
        if proof.get("status") not in UNRESOLVED_PROOF_STATUSES:
            continue
        proof_code = proof_point_code(proof)
        if proof_code and proof_code not in missing_evidence:
            missing_evidence.append(proof_code)

    if unresolved_funding_claim_requires_proof(case_board) and (
        "funding_proof" not in missing_evidence
    ):
        missing_evidence.append("funding_proof")
    return missing_evidence


def unresolved_funding_claim_requires_proof(case_board: dict[str, Any]) -> bool:
    claims = _list_payload(case_board.get("claims"))
    funding_claims = [
        claim
        for claim in claims
        if claim.get("field_path") == "/funding/primary_source"
        and claim.get("status") in {"stated", "unknown"}
        and _has_value(claim.get("value"))
    ]
    if not funding_claims:
        return False

    evidence_cards = _list_payload(case_board.get("evidence_cards"))
    uploaded_evidence_ids = {
        evidence_id
        for evidence in evidence_cards
        if evidence.get("source_type") == "uploaded_file"
        for evidence_id in [evidence.get("evidence_id")]
        if isinstance(evidence_id, str) and evidence_id.strip()
    }
    uploaded_evidence_claim_refs = {
        claim_ref
        for evidence in evidence_cards
        if evidence.get("source_type") == "uploaded_file"
        for claim_ref in _string_list(evidence.get("claim_refs"))
    }
    if any(
        claim.get("field_path") == "/funding/primary_source"
        and claim.get("status") == "documented"
        and (
            claim.get("claim_id") in uploaded_evidence_claim_refs
            or bool(
                set(_string_list(claim.get("supporting_evidence_ids"))).intersection(
                    uploaded_evidence_ids
                )
            )
        )
        for claim in claims
    ):
        return False

    for claim in funding_claims:
        supporting_refs = _string_list(claim.get("supporting_evidence_ids"))
        if claim.get("claim_id") in uploaded_evidence_claim_refs:
            continue
        if set(supporting_refs).intersection(uploaded_evidence_ids):
            continue
        return True
    return False


def proof_point_code(proof: dict[str, Any]) -> str | None:
    normalized_type = _normalized_document_type(proof.get("document_type"))
    if normalized_type:
        return normalized_type

    metadata = proof.get("metadata")
    if isinstance(metadata, dict):
        normalized_type = _normalized_document_type(
            metadata.get("document_type")
            or metadata.get("required_document")
            or metadata.get("proof_document_type")
        )
        if normalized_type:
            return normalized_type

    for key in ("proof_point_id", "question"):
        value = proof.get(key)
        if isinstance(value, str) and value.strip():
            normalized_type = _normalized_document_type(value)
            return normalized_type or value.strip()
    return None


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _normalized_document_type(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold().replace("-", "_").replace(" ", "_")
    if not normalized:
        return None
    if "funding_proof" in normalized or any(
        marker in normalized
        for marker in (
            "bank_statement",
            "financial_statement",
            "sponsor_letter",
            "affidavit_of_support",
            "scholarship_letter",
        )
    ):
        return "funding_proof"
    if normalized in {"ds160", "passport_bio", "i20", "admission_letter"}:
        return normalized
    return None


def _has_value(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item.strip() for item in value if isinstance(item, str) and item.strip()]
