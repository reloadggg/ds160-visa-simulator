from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core import settings as settings_module
from app.db.session import get_db
from app.services.material_package_archive_service import (
    MaterialPackageArchiveService,
)

router = APIRouter(prefix="/v1", tags=["material-packages"])


def _ensure_material_package_archive_enabled() -> None:
    if not settings_module.settings.allow_debug_fill:
        raise HTTPException(
            status_code=403,
            detail="material package archive is disabled because debug fill is disabled",
        )


@router.get("/material-packages")
def list_material_packages(db: Session = Depends(get_db)) -> dict:
    _ensure_material_package_archive_enabled()
    return MaterialPackageArchiveService(db).list_packages()


@router.post("/sessions/{session_id}/material-packages/{package_id}/import")
def import_material_package(
    session_id: str,
    package_id: str,
    db: Session = Depends(get_db),
) -> dict:
    _ensure_material_package_archive_enabled()
    try:
        return MaterialPackageArchiveService(db).import_package(session_id, package_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
