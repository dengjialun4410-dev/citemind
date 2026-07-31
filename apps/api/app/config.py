from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_name: str = "CiteMind API"
    database_url: str = "sqlite:///./citemind.db"
    upload_dir: Path = Path("./uploads")
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    openai_chat_model: str = "gpt-4.1-mini"
    chunk_size: int = 900
    chunk_overlap: int = 140
    retrieval_top_k: int = 5
    embedding_provider: str = "hashing"
    embedding_model: str = "text-embedding-3-small"
    embedding_dimensions: int = 384
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    celery_broker_url: str = "redis://localhost:6379/0"
    celery_result_backend: str = "redis://localhost:6379/1"
    celery_task_always_eager: bool = True
    jwt_secret_key: str = "change-me-before-production"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 1440
    demo_seed_enabled: bool = True
    demo_user_email: str = "demo@citemind.dev"
    demo_user_password: str = "CiteMind123!"

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
