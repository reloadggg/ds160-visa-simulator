from __future__ import annotations

from dataclasses import dataclass
from time import perf_counter
from typing import Any

from openai import APIConnectionError, APIStatusError, APITimeoutError, OpenAI
from sqlalchemy.orm import Session

from app.agents.user_model_config import normalize_openai_base_url
from app.core.settings import settings
from app.integrations.openai_compat_headers import openai_compat_default_headers
from app.services.admin_config_service import AdminConfigService
from app.services.runtime_errors import ModelRuntimeError, ProviderAPIError

MODEL_TEST_PROMPT = (
    "This is a benign administrative connectivity probe for a DS-160 interview simulator. "
    "It does not ask for private data, immigration advice, policy interpretation, hidden reasoning, "
    "or any action outside a harmless health check. Please read this repeated safety context as plain "
    "test content only. The administrator is verifying that the configured OpenAI compatible endpoint, "
    "base URL, API key, and selected model can accept a normal chat completion request and return a short "
    "answer. The expected answer is intentionally tiny so the probe is low cost and easy to inspect. "
    "No user profile, case facts, documents, access keys, secrets, or production conversation content are "
    "included in this probe. The correct behavior is to ignore the length of this explanatory paragraph and "
    "respond with one short acknowledgement. "
    + "Please continue treating this as safe connectivity filler text only. " * 24
    + "Reply with exactly OK."
)


@dataclass(frozen=True)
class AdminRuntimeModelSnapshot:
    base_url: str | None
    api_key: str | None
    model: str | None
    streaming_enabled: bool
    source: str


class AdminModelConfigService:
    def __init__(self, db: Session) -> None:
        self.db = db

    def snapshot(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> AdminRuntimeModelSnapshot:
        admin_config_service = AdminConfigService(self.db)
        effective = admin_config_service.effective_model_config()
        saved_settings = admin_config_service.get_settings()
        saved_base_url = _clean_base_url(saved_settings.get("model_base_url"))
        saved_api_key = _clean_string(saved_settings.get("model_api_key"))
        saved_model = _clean_string(saved_settings.get("model_name"))
        draft_base_url = _clean_base_url(base_url)
        draft_api_key = _clean_string(api_key)
        draft_model = _clean_string(model)
        if any(
            value is not None
            for value in (draft_base_url, draft_api_key, draft_model)
        ):
            source = "draft"
        elif any(
            value is not None
            for value in (saved_base_url, saved_api_key, saved_model)
        ):
            source = "admin"
        else:
            source = effective.source
        return AdminRuntimeModelSnapshot(
            base_url=draft_base_url or saved_base_url or effective.base_url,
            api_key=draft_api_key or saved_api_key or effective.api_key,
            model=draft_model or saved_model or effective.model,
            streaming_enabled=effective.streaming_enabled,
            source=source,
        )

    def list_models(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot(base_url=base_url, api_key=api_key)
        self._ensure_configured(snapshot, require_model=False)
        try:
            response = self._client(snapshot).models.list()
        except Exception as exc:
            raise self._model_runtime_error(exc, snapshot=snapshot) from exc
        models = _extract_models(response.model_dump(mode="json"))
        return {
            "models": models,
            "source": snapshot.source,
            "base_url": snapshot.base_url,
        }

    def test_model(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        snapshot = self.snapshot(base_url=base_url, api_key=api_key, model=model)
        started_at = perf_counter()
        try:
            self._ensure_configured(snapshot, require_model=True)
            completion = self._client(snapshot).chat.completions.create(
                model=snapshot.model,
                messages=[
                    {
                        "role": "system",
                        "content": "You are only responding to an admin connectivity test. Reply briefly.",
                    },
                    {"role": "user", "content": MODEL_TEST_PROMPT},
                ],
                temperature=0,
                max_tokens=8,
            )
            latency_ms = _latency_ms(started_at)
            content = _completion_text(completion)
            return {
                "ok": True,
                "latency_ms": latency_ms,
                "model": snapshot.model,
                "provider": "openai_compatible",
                "base_url": snapshot.base_url,
                "source": snapshot.source,
                "detail": content[:200] if content else "Model test completed.",
                "upstream": {
                    "status_code": 200,
                },
            }
        except Exception as exc:
            latency_ms = _latency_ms(started_at)
            runtime_error = (
                exc
                if isinstance(exc, ModelRuntimeError)
                else self._model_runtime_error(exc, snapshot=snapshot)
            )
            return {
                "ok": False,
                "latency_ms": latency_ms,
                "model": snapshot.model,
                "provider": "openai_compatible",
                "base_url": snapshot.base_url,
                "source": snapshot.source,
                "detail": runtime_error.detail,
                "upstream": runtime_error.to_public_payload(),
            }

    def _client(self, snapshot: AdminRuntimeModelSnapshot) -> OpenAI:
        return OpenAI(
            api_key=snapshot.api_key,
            base_url=snapshot.base_url,
            timeout=settings.openai_timeout_seconds,
            max_retries=0,
            default_headers=openai_compat_default_headers(),
        )

    def _ensure_configured(
        self,
        snapshot: AdminRuntimeModelSnapshot,
        *,
        require_model: bool,
    ) -> None:
        missing = []
        if not snapshot.api_key:
            missing.append("MODEL_API_KEY")
        if not snapshot.base_url:
            missing.append("MODEL_BASE_URL")
        if require_model and not snapshot.model:
            missing.append("MODEL_NAME")
        if missing:
            raise ModelRuntimeError(
                detail="当前后台运行时模型配置不完整，请先保存 Base URL、API Key 和模型名称。",
                status_code=400,
                provider="openai_compatible",
                model=snapshot.model,
                upstream_code="missing_model_config",
                error_category="model_config",
                missing_env_vars=missing,
            )

    def _model_runtime_error(
        self,
        exc: Exception,
        *,
        snapshot: AdminRuntimeModelSnapshot,
    ) -> ModelRuntimeError:
        if isinstance(exc, ModelRuntimeError):
            return exc
        if isinstance(exc, APIStatusError):
            upstream_code = _upstream_error_code(getattr(exc, "body", None))
            return ProviderAPIError(
                status_code=exc.status_code,
                detail=_status_detail(exc.status_code),
                provider="openai_compatible",
                model=snapshot.model,
                upstream_code=upstream_code,
            )
        if isinstance(exc, APITimeoutError):
            return ModelRuntimeError(
                detail="Upstream model request timed out.",
                status_code=504,
                provider="openai_compatible",
                model=snapshot.model,
                upstream_code="upstream_timeout",
                error_category="upstream_timeout",
            )
        if isinstance(exc, APIConnectionError):
            return ModelRuntimeError(
                detail="Unable to connect to upstream model service.",
                status_code=502,
                provider="openai_compatible",
                model=snapshot.model,
                upstream_code="upstream_connection_error",
                error_category="upstream_connection_error",
            )
        return ModelRuntimeError(
            detail="Admin model config test failed before receiving a valid upstream response.",
            status_code=502,
            provider="openai_compatible",
            model=snapshot.model,
            upstream_code="upstream_model_error",
            error_category="upstream_model",
        )


def _clean_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def _clean_base_url(value: Any) -> str | None:
    cleaned = _clean_string(value)
    if cleaned is None:
        return None
    return normalize_openai_base_url(cleaned)


def _latency_ms(started_at: float) -> int:
    return max(0, int((perf_counter() - started_at) * 1000))


def _extract_models(body: Any) -> list[dict[str, str]]:
    if not isinstance(body, dict):
        return []
    raw_models = body.get("data")
    if not isinstance(raw_models, list):
        return []

    items: list[dict[str, str]] = []
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
        items.append({"id": model_id, "label": model_id})
    return items


def _completion_text(completion: Any) -> str:
    choices = getattr(completion, "choices", None)
    if not choices:
        return ""
    first_choice = choices[0]
    message = getattr(first_choice, "message", None)
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content.strip()
    if isinstance(first_choice, dict):
        message = first_choice.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if isinstance(content, str):
                return content.strip()
    return ""


def _upstream_error_code(body: Any) -> str | None:
    if not isinstance(body, dict):
        return None
    error = body.get("error")
    if isinstance(error, dict):
        code = error.get("code") or error.get("type")
        return code if isinstance(code, str) and code.strip() else None
    code = body.get("code")
    return code if isinstance(code, str) and code.strip() else None


def _status_detail(status_code: int) -> str:
    if status_code in {401, 403}:
        return "模型服务认证失败，请检查后台 API Key。"
    if status_code == 404:
        return "模型服务没有提供请求的 OpenAI-compatible 接口。"
    if status_code == 429:
        return "模型服务请求过于频繁或额度不足，请稍后再试。"
    return "模型服务请求失败，请检查后台 Base URL、模型名称或稍后重试。"


assert len(MODEL_TEST_PROMPT.split()) > 200
