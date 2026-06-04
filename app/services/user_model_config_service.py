from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator
from sqlalchemy.orm import Session

from app.agents.user_model_config import UserModelConfig, normalize_openai_base_url
from app.db.session import SessionLocal
from app.services.admin_config_service import AdminConfigService


class UserModelConfigPayload(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr = Field(min_length=1)
    model: str = Field(min_length=1, max_length=200)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_openai_base_url(value)

    @field_validator("model")
    @classmethod
    def validate_model(cls, value: str) -> str:
        stripped = value.strip()
        if not stripped:
            raise ValueError("模型名称不能为空。")
        return stripped


def ensure_user_model_config_enabled(db: Session | None = None) -> None:
    if db is not None:
        admin_enabled = AdminConfigService(db).user_model_config_enabled()
    else:
        with SessionLocal() as local_db:
            admin_enabled = AdminConfigService(local_db).user_model_config_enabled()
    if not admin_enabled:
        raise PermissionError("当前部署未启用用户自定义模型配置。")


def to_runtime_config(
    payload: UserModelConfigPayload | None,
    db: Session | None = None,
) -> UserModelConfig | None:
    if payload is None:
        return None
    ensure_user_model_config_enabled(db)
    return UserModelConfig(
        base_url=payload.base_url,
        api_key=payload.api_key.get_secret_value(),
        model=payload.model,
    )
