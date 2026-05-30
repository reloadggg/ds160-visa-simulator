from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.document_repo import DocumentRepository
from app.repositories.session_repo import SessionRepository
from app.services.case_memory_service import CaseMemoryService
from app.services.interview_review_service import InterviewReviewService
from app.services.report_service import ReportService
from app.services.session_read_model_service import SessionReadModelService

router = APIRouter(prefix="/v1/sessions/{session_id}/reports", tags=["reports"])


@router.get("/user")
def get_user_report(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict:
    record = SessionRepository(db).get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    read_model = SessionReadModelService(db).build_from_record(record)
    case_board = CaseMemoryService(db).public_case_board(session_id)
    return ReportService().user_report(
        session_id=session_id,
        visa_family=record.declared_family or "unknown",
        governor_decision=record.current_governor_decision,
        profile_json=record.profile_json,
        phase_state=record.phase_state,
        gate_status=record.gate_status_json,
        runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
        interviewer_state_json=record.interviewer_state_json,
        current_focus_json=record.current_focus_json,
        case_board=case_board,
    )


@router.get("/internal")
def get_internal_report(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict:
    record = SessionRepository(db).get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    read_model = SessionReadModelService(db).build_from_record(record)
    case_board = CaseMemoryService(db).public_case_board(session_id)
    return ReportService().internal_report(
        session_id=session_id,
        visa_family=record.declared_family or "unknown",
        governor_decision=record.current_governor_decision,
        profile_json=record.profile_json,
        runtime_ledger=read_model.runtime_ledger.model_dump(mode="json"),
        runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
        runtime_trace=record.runtime_trace_json,
        score_history=record.score_history_json,
        governor_history=record.governor_history_json,
        interviewer_state_json=record.interviewer_state_json,
        current_focus_json=record.current_focus_json,
        case_board=case_board,
    )


@router.post("/review")
def generate_interview_review(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict:
    try:
        return InterviewReviewService(db).generate(session_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/export")
def export_session_report(
    session_id: str,
    db: Session = Depends(get_db),
) -> dict:
    record = SessionRepository(db).get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")

    read_model = SessionReadModelService(db).build_from_record(record)
    case_memory = CaseMemoryService(db)
    case_board = case_memory.public_case_board(session_id)
    documents = DocumentRepository(db).list_session_document_exports(session_id)
    user_report = ReportService().user_report(
        session_id=session_id,
        visa_family=record.declared_family or "unknown",
        governor_decision=record.current_governor_decision,
        profile_json=record.profile_json,
        phase_state=record.phase_state,
        gate_status=record.gate_status_json,
        runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
        interviewer_state_json=record.interviewer_state_json,
        current_focus_json=record.current_focus_json,
        case_board=case_board,
    )
    internal_report = ReportService().internal_report(
        session_id=session_id,
        visa_family=record.declared_family or "unknown",
        governor_decision=record.current_governor_decision,
        profile_json=record.profile_json,
        runtime_ledger=read_model.runtime_ledger.model_dump(mode="json"),
        runtime_view_state=read_model.runtime_view_state.model_dump(mode="json"),
        runtime_trace=record.runtime_trace_json,
        score_history=record.score_history_json,
        governor_history=record.governor_history_json,
        interviewer_state_json=record.interviewer_state_json,
        current_focus_json=record.current_focus_json,
        case_board=case_board,
    )

    return {
        "schema_version": "ds160.session_export.v1",
        "session": {
            "session_id": record.session_id,
            "phase_state": record.phase_state,
            "declared_family": record.declared_family,
            "current_governor_decision": record.current_governor_decision,
            "gate_status": record.gate_status_json,
            "current_focus": record.current_focus_json,
        },
        "reports": {
            "user": user_report,
            "internal": internal_report,
        },
        "profile_snapshot": record.profile_json,
        "documents": [
            {
                "document_id": document.document_id,
                "filename": document.filename,
                "status": document.status,
                "extracted_text": document.raw_text or "",
                "artifact": case_memory.sanitize_public_payload(
                    document.artifact_json or {}
                ),
            }
            for document in documents
        ],
    }
