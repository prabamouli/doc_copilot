from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"
    openai_base_url: str | None = None
    app_name: str = "Clinic Copilot MVP"
    database_url: str = "sqlite:///./clinic_copilot.db"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
