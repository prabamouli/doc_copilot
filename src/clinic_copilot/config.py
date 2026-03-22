from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    openai_api_key: str = ""
    openai_model: str = "llama3.2:1b"
    openai_base_url: str | None = None
    litellm_master_key: str = "sk-local"
    ollama_model: str = "llama3.2:1b"
    app_name: str = "Clinic Copilot MVP"
    database_url: str = "sqlite:///./clinic_copilot.db"
    vector_database_url: str = ""
    vault_encryption_secret: str = "clinic-copilot-vault-secret"
    whisper_model: str = "base"
    pgvector_records_table: str = "patient_records"
    embedding_model: str = "sentence-transformers/all-mpnet-base-v2"
    embedding_dimension: int = 768
    history_reranker_model: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"
    history_candidate_pool_size: int = 10
    nli_model: str = "MoritzLaurer/DeBERTa-v3-base-mnli-fever-anli"
    nli_entailment_threshold: float = 0.62
    nli_contradiction_threshold: float = 0.55
    allow_remote_model_downloads: bool = False

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")


settings = Settings()
