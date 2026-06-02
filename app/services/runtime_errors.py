from __future__ import annotations


class ModelRuntimeError(RuntimeError):
    def __init__(
        self,
        *,
        detail: str,
        status_code: int = 503,
        provider: str | None = None,
        model: str | None = None,
        upstream_code: str | None = None,
        error_category: str | None = None,
        body: object | None = None,
        missing_env_vars: list[str] | None = None,
    ) -> None:
        self.detail = detail
        self.status_code = status_code
        self.provider = provider
        self.model = model
        self.upstream_code = upstream_code
        self.error_category = error_category or self._default_error_category(
            status_code=status_code,
            upstream_code=upstream_code,
        )
        self.body = body
        self.missing_env_vars = list(missing_env_vars or [])
        super().__init__(detail)

    @staticmethod
    def _default_error_category(
        *,
        status_code: int,
        upstream_code: str | None,
    ) -> str:
        if upstream_code == "missing_model_config":
            return "model_config"
        if upstream_code == "upstream_timeout" or status_code == 504:
            return "upstream_timeout"
        if upstream_code == "upstream_connection_error":
            return "upstream_connection_error"
        if upstream_code == "model_output_invalid":
            return "model_output_invalid"
        if upstream_code == "agent_runtime_error":
            return "agent_runtime_error"
        if status_code in {401, 403, 429, 500, 502, 503}:
            return "upstream_model"
        return "model_runtime"

    def to_public_payload(self) -> dict:
        payload = {
            "status": self.status_code,
            "detail": self.detail,
            "error_category": self.error_category,
            "upstream_code": self.upstream_code,
            "provider": self.provider,
            "model": self.model,
            "missing_env_vars": self.missing_env_vars,
        }
        return {key: value for key, value in payload.items() if value not in (None, [], {})}


class ModelUnavailableError(ModelRuntimeError):
    def __init__(
        self,
        *,
        detail: str,
        provider: str | None = None,
        model: str | None = None,
        missing_env_vars: list[str] | None = None,
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=503,
            provider=provider,
            model=model,
            upstream_code="missing_model_config",
            error_category="model_config",
            missing_env_vars=missing_env_vars,
        )


class ProviderAPIError(ModelRuntimeError):
    def __init__(
        self,
        *,
        status_code: int,
        detail: str,
        provider: str | None = None,
        model: str | None = None,
        upstream_code: str | None = None,
        body: object | None = None,
    ) -> None:
        super().__init__(
            detail=detail,
            status_code=status_code,
            provider=provider,
            model=model,
            upstream_code=upstream_code,
            error_category=(
                "upstream_timeout"
                if status_code == 504 or upstream_code == "upstream_timeout"
                else "upstream_model"
            ),
            body=body,
        )
