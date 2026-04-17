from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "DS-160 Visa Simulator"
    database_url: str = "sqlite:///./app.sqlite3"
    llm_provider: str = "openai"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")


settings = Settings()
