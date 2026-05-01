from functools import lru_cache
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "PMO Agent Office"
    app_env: str = "local"
    api_key: str = "local-dev-key"
    log_level: str = "INFO"
    feishu_app_id: str | None = None
    feishu_app_secret: str | None = None
    feishu_app_token: str | None = None
    feishu_base_table_config: Path = Path("config/base_tables.yaml")
    feishu_request_timeout_seconds: int = 10
    feishu_max_retries: int = 3
    llm_provider: str | None = None
    llm_api_key: str | None = None
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_timeout_seconds: int = 30
    langsmith_tracing: bool = False
    langsmith_api_key: str | None = None
    langsmith_project: str = "pmo-agent-office"
    trace_local_dir: Path = Path("traces")
    memory_local_file: Path = Path("memory/session_runs.jsonl")
    agent_max_steps: int = Field(default=5, ge=1, le=20)


@lru_cache
def get_settings() -> Settings:
    """Return cached settings for dependency injection."""

    return Settings()
