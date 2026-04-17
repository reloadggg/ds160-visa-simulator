from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DS-160 Visa Simulator"
    database_url: str = "sqlite:///./app.sqlite3"
    llm_provider: str = "openai"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout_seconds: float = 60.0
    run_live_llm_tests: bool = False

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
