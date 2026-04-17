from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from app.core.visa_families import validate_declared_family
from app.core.dependencies import get_session_repo
from app.repositories.session_repo import SessionRepository
from app.services.gate_service import GateService

router = APIRouter(prefix="/v1/sessions", tags=["sessions"])


class CreateSessionRequest(BaseModel):
    declared_family: str | None = None


@router.post("", status_code=201)
def create_session(
    payload: CreateSessionRequest,
    repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    try:
        declared_family = validate_declared_family(payload.declared_family)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    record = repo.create(declared_family)
    return {
        "session_id": record.session_id,
        "phase_state": record.phase_state,
        "current_governor_decision": record.current_governor_decision,
    }


@router.get("/{session_id}/required-package")
def get_required_package(
    session_id: str,
    repo: SessionRepository = Depends(get_session_repo),
) -> dict:
    record = repo.get(session_id)
    if record is None:
        raise HTTPException(status_code=404, detail="session not found")
    if record.declared_family is None:
        raise HTTPException(status_code=409, detail="declared_family not locked")
    try:
        declared_family = validate_declared_family(record.declared_family)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    required = GateService().required_package(declared_family)
    return {"required_initial_package": required}
