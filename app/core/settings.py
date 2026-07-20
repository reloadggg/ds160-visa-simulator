from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


class Settings(BaseSettings):
    app_name: str = "DS-160 Visa Simulator"
    log_level: str = "INFO"
    log_format: Literal["text", "json"] = "json"
    database_url: str = "sqlite:///./app.sqlite3"
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout_seconds: float = 60.0
    openai_compat_user_agent: str = "curl/8.5.0"
    ai_material_bundle_timeout_seconds: float = 180.0
    run_live_llm_tests: bool = False
    app_auth_password: str | None = None
    app_auth_session_ttl_seconds: int = 60 * 60 * 24
    app_auth_idle_timeout_seconds: int = 60 * 60 * 8
    app_auth_cookie_name: str = "ds160_session"
    app_auth_cookie_secure: bool = True
    app_auth_cookie_samesite: str = "lax"
    app_auth_cookie_domain: str | None = None
    admin_auth_password: str | None = None
    admin_auth_cookie_name: str = "ds160_admin_session"
    admin_auth_session_ttl_seconds: int = 60 * 60 * 24
    app_auth_login_rate_limit_attempts: int = 5
    app_auth_login_rate_limit_window_seconds: int = 60
    app_auth_touch_interval_seconds: int = 60
    app_auth_csrf_protection: bool = True
    app_auth_protect_docs: bool = True
    app_auth_password_user_fallback_enabled: bool = False
    app_compat_api_key: str | None = None
    # When false (default), ignore client-supplied X-Forwarded-For / X-Real-IP
    # for rate-limit and audit IP attribution; use the direct TCP peer only.
    # Prefer CF-Connecting-IP when present regardless of this flag.
    # Enable only when the app is behind a reverse proxy that overwrites/appends
    # these headers and untrusted clients cannot reach the origin directly.
    trust_x_forwarded_for: bool = False
    allow_debug_fill: bool = False
    allow_runtime_debug: bool = False
    allow_user_model_config: bool = False
    allow_user_model_streaming: bool = True
    agent_runtime: Literal[
        "legacy",
        "graph_shadow",
        "graph_canary",
        "graph",
        "native_interviewer",
    ] = "native_interviewer"
    agent_runtime_canary_percent: int = 0
    agent_runtime_trace_enabled: bool = True
    agent_runtime_typed_adjudication_enabled: bool = True
    rag_enabled: bool = False
    rag_vector_store: str = "chroma"
    rag_index_version: str = "v1"
    rag_chroma_mode: str = "persistent"
    rag_chroma_path: str = "./data/chroma/us_visa"
    rag_chroma_host: str = "localhost"
    rag_chroma_port: int = 8000
    rag_chroma_ssl: bool = False
    rag_collection_prefix: str = "us_visa"
    siliconflow_base_url: str = "https://api.siliconflow.com/v1"
    siliconflow_api_key: str | None = None
    siliconflow_embedding_model: str = "BAAI/bge-m3"
    siliconflow_embedding_dimensions: int | None = None
    siliconflow_embedding_batch_size: int = 32
    siliconflow_rerank_model: str = "Qwen/Qwen3-Reranker-4B"
    rag_vector_top_k_per_collection: int = 8
    rag_candidate_limit: int = 24
    rag_rerank_top_n: int = 6
    rag_min_final_score: float = 0.15
    rag_max_context_chars: int = 5000
    rag_chunk_size: int = 900
    rag_chunk_overlap: int = 150
    rag_upload_max_size_mb: int = 32
    rag_allow_third_party_reference: bool = False
    rag_source_manifest: str = "docs/rag/us-visa-source-manifest-100plus.md"
    rag_raw_doc_dir: str = "data/rag/us_visa/raw"
    # When True (default), gate readiness requires material understanding
    # completed (or skipped_legacy). Set False for offline demos where
    # parsed+legacy evidence is enough.
    material_understanding_required: bool = True

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @field_validator("siliconflow_embedding_dimensions", mode="before")
    @classmethod
    def empty_embedding_dimensions_as_none(cls, value: object) -> object:
        if value == "":
            return None
        return value

    @field_validator("agent_runtime_canary_percent")
    @classmethod
    def validate_agent_runtime_canary_percent(cls, value: int) -> int:
        if value < 0:
            return 0
        if value > 100:
            return 100
        return value

    @property
    def cors_allow_origins_list(self) -> list[str]:
        return [
            origin.strip()
            for origin in self.cors_allow_origins.split(",")
            if origin.strip()
        ]

    @property
    def app_auth_enabled(self) -> bool:
        return bool(self.app_auth_password)

    @property
    def admin_auth_enabled(self) -> bool:
        return bool(self.admin_auth_password or self.app_auth_password)

    @property
    def effective_admin_auth_password(self) -> str | None:
        return self.admin_auth_password or self.app_auth_password

    @property
    def app_auth_docs_public(self) -> bool:
        return not self.app_auth_protect_docs

    @property
    def siliconflow_embedding_dimensions_supported(self) -> bool:
        return self.siliconflow_embedding_model.startswith("Qwen/Qwen3-Embedding-")

    @property
    def rag_ready(self) -> bool:
        return self.rag_enabled and self.rag_skip_reason is None

    @property
    def rag_skip_reason(self) -> str | None:
        if not self.rag_enabled:
            return "disabled"
        if not self.siliconflow_api_key:
            return "missing_siliconflow_api_key"
        if (
            self.siliconflow_embedding_dimensions is not None
            and not self.siliconflow_embedding_dimensions_supported
        ):
            return "embedding_dimensions_unsupported"
        if self.rag_vector_store != "chroma":
            return "unsupported_vector_store"
        return None


settings = Settings()
