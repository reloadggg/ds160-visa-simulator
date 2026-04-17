from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
    record = repo.create(payload.declared_family)
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

    required = GateService().required_package(record.declared_family or "f1")
    return {"required_initial_package": required}
