from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.dependencies import require_session_access
from app.db.session import get_db
from app.services.admin_config_service import AdminConfigService
from app.services.material_package_archive_service import (
    MaterialPackageArchiveService,
)

router = APIRouter(prefix="/v1", tags=["material-packages"])


def _ensure_material_package_archive_enabled(db: Session) -> None:
    if not AdminConfigService(db).debug_material_enabled():
        raise HTTPException(
            status_code=403,
            detail="material package archive is disabled because debug fill is disabled",
        )


@router.get("/material-packages")
def list_material_packages(db: Session = Depends(get_db)) -> dict:
    _ensure_material_package_archive_enabled(db)
    return MaterialPackageArchiveService(db).list_packages()


@router.post("/sessions/{session_id}/material-packages/{package_id}/import")
def import_material_package(
    session_id: str,
    package_id: str,
    _: None = Depends(require_session_access),
    db: Session = Depends(get_db),
) -> dict:
    _ensure_material_package_archive_enabled(db)
    try:
        return MaterialPackageArchiveService(db).import_package(session_id, package_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
