from __future__ import annotations

from pydantic import BaseModel, Field, SecretStr, field_validator

from app.agents.user_model_config import UserModelConfig, normalize_openai_base_url
from app.core.settings import settings


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


def ensure_user_model_config_enabled() -> None:
    if not settings.allow_user_model_config:
        raise PermissionError("当前部署未启用用户自定义模型配置。")


def to_runtime_config(payload: UserModelConfigPayload | None) -> UserModelConfig | None:
    if payload is None:
        return None
    ensure_user_model_config_enabled()
    return UserModelConfig(
        base_url=payload.base_url,
        api_key=payload.api_key.get_secret_value(),
        model=payload.model,
    )
