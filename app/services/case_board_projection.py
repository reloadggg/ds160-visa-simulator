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
    return missing_evidence


def proof_point_code(proof: dict[str, Any]) -> str | None:
    for key in ("proof_point_id", "question"):
        value = proof.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _list_payload(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]
