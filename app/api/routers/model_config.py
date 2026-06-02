from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from pydantic import BaseModel, Field, SecretStr, field_validator

from app.agents.user_model_config import normalize_openai_base_url
from app.core.settings import settings
from app.integrations.openai_compat_headers import openai_compat_default_headers
from app.services.user_model_config_service import ensure_user_model_config_enabled


router = APIRouter(prefix="/v1/model-config", tags=["model-config"])


class ModelListRequest(BaseModel):
    base_url: str = Field(min_length=1, max_length=2048)
    api_key: SecretStr = Field(min_length=1)

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        return normalize_openai_base_url(value)


class ModelListItem(BaseModel):
    id: str
    label: str


class ModelListResponse(BaseModel):
    models: list[ModelListItem]


@router.post("/models")
def list_user_models(payload: ModelListRequest) -> ModelListResponse:
    try:
        ensure_user_model_config_enabled()
    except PermissionError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    try:
        response = OpenAI(
            api_key=payload.api_key.get_secret_value(),
            base_url=payload.base_url,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
            default_headers=openai_compat_default_headers(),
        ).models.list()
    except APIStatusError as exc:
        status_code = exc.status_code
        if status_code in {401, 403}:
            detail = "模型服务认证失败，请检查 API Key。"
        elif status_code == 404:
            detail = "模型服务没有提供 /v1/models 列表接口，请手动输入模型名称。"
        elif status_code == 429:
            detail = "模型服务请求过于频繁或额度不足，请稍后再试。"
        else:
            detail = "模型列表获取失败，请检查 Base URL 或稍后重试。"
        raise HTTPException(status_code=status_code, detail=detail) from exc
    except (APIConnectionError, APITimeoutError) as exc:
        raise HTTPException(
            status_code=502,
            detail="无法连接模型服务，请检查 Base URL。",
        ) from exc

    body = response.model_dump(mode="json")
    models = _extract_models(body)
    return ModelListResponse(models=models)


def _extract_models(body: Any) -> list[ModelListItem]:
    if not isinstance(body, dict):
        return []
    raw_models = body.get("data")
    if not isinstance(raw_models, list):
        return []

    items: list[ModelListItem] = []
    seen: set[str] = set()
    for raw_model in raw_models:
        if not isinstance(raw_model, dict):
            continue
        model_id = raw_model.get("id")
        if not isinstance(model_id, str):
            continue
        model_id = model_id.strip()
        if not model_id or model_id in seen:
            continue
        seen.add(model_id)
        items.append(ModelListItem(id=model_id, label=model_id))
    return items
