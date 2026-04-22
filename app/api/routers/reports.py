from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.session_repo import SessionRepository
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
    )
