from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.admin_config_service import AdminConfigService

router = APIRouter(prefix="/v1/app-config", tags=["app-config"])


@router.get("")
def get_app_config(db: Session = Depends(get_db)) -> dict:
    return AdminConfigService(db).public_app_config()
