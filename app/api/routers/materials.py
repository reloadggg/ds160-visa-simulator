from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from app.core.simple_auth import get_current_admin_session, get_current_auth_session
from app.db.session import get_db
from app.services.material_cleanup_service import MaterialCleanupService

router = APIRouter(prefix="/v1/materials", tags=["materials"])


@router.delete("/current-key")
def clear_current_key_materials(
    request: Request,
    db: Session = Depends(get_db),
) -> dict:
    if get_current_admin_session(request, db, touch=False) is not None:
        raise HTTPException(
            status_code=403,
            detail="admin session cannot use current-key material cleanup",
        )

    current_auth = get_current_auth_session(request, db, touch=False)
    if current_auth is None:
        raise HTTPException(status_code=401, detail="authentication required")
    if not current_auth.access_key_id:
        raise HTTPException(
            status_code=400,
            detail="current auth session is not bound to an access key",
        )

    try:
        result = MaterialCleanupService(db).clear_access_key_materials(
            current_auth.access_key_id,
        )
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    db.commit()
    return result.to_payload()
