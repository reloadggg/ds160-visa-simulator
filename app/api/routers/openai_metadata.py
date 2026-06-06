from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.services.case_memory_service import CaseMemoryService
from app.services.runtime_view_contract_service import RuntimeViewContractService
from app.services.session_read_model_service import SessionReadModelService


def build_openai_adapter_metadata(
    db: Session,
    *,
    session_record,
    result: dict[str, Any],
    context_mode: str,
) -> dict[str, Any]:
    """Build shared metadata for product OpenAI-compatible inbound adapters."""

    read_model = SessionReadModelService(db).build_from_record(session_record)
    case_memory = CaseMemoryService(db)
    case_board = case_memory.public_case_board(session_record.session_id)
    evidence_graph = case_memory.public_evidence_graph(session_record.session_id)
    runtime_view_state = RuntimeViewContractService.payload(
        read_model.runtime_view_state,
        anchored_only=True,
    )
    return {
        "session_id": session_record.session_id,
        "phase_state": read_model.phase_state,
        "context_mode": context_mode,
        "governor_decision": RuntimeViewContractService.governor_decision(
            runtime_view_state,
            result,
        ),
        "requested_documents": RuntimeViewContractService.requested_documents(
            runtime_view_state,
            result,
        ),
        "remaining_required_documents": (
            RuntimeViewContractService.remaining_required_documents(
                runtime_view_state,
                result,
            )
        ),
        "turn_decision": RuntimeViewContractService.turn_decision(
            runtime_view_state,
            result,
        ),
        "document_review": RuntimeViewContractService.document_review(
            runtime_view_state,
            result,
        ),
        "case_board": case_board,
        "evidence_graph": evidence_graph,
        "prompt_trace": RuntimeViewContractService.prompt_trace(
            runtime_view_state,
            result,
        ),
        "runtime_view_state": runtime_view_state,
        "agent_runtime": result.get("agent_runtime"),
        "selected_public_runtime": result.get("selected_public_runtime"),
        "runtime_execution": result.get("runtime_execution"),
        "native_run_id": result.get("native_run_id"),
    }
