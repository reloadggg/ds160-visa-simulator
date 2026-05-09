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
        body: object | None = None,
        missing_env_vars: list[str] | None = None,
    ) -> None:
        self.detail = detail
        self.status_code = status_code
        self.provider = provider
        self.model = model
        self.upstream_code = upstream_code
        self.body = body
        self.missing_env_vars = list(missing_env_vars or [])
        super().__init__(detail)


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
            body=body,
        )
