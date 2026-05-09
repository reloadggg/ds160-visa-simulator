from pathlib import Path

from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict


load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=False)


class Settings(BaseSettings):
    app_name: str = "DS-160 Visa Simulator"
    database_url: str = "sqlite:///./app.sqlite3"
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout_seconds: float = 60.0
    run_live_llm_tests: bool = False
    app_auth_password: str | None = None
    app_auth_token_ttl_seconds: int = 60 * 60 * 24

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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


settings = Settings()
