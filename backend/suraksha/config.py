"""Runtime settings, read from the environment and .env."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=REPO_ROOT / ".env", extra="ignore")

    model_api_key: str = ""
    model_base_url: str = "https://api.meta.ai/v1"
    llm_mode: Literal["live", "record", "replay"] = "replay"
    llm_recordings_dir: Path = REPO_ROOT / "evals" / "recordings"
    llm_spend_limit_usd: float = 120.0

    database_url: str = "postgresql+psycopg://suraksha:suraksha@localhost:5433/suraksha"

    temporal_address: str = "localhost:7233"
    temporal_namespace: str = "default"
    temporal_task_queue: str = "suraksha"

    vault_key: str = ""
    docstore_dir: Path = REPO_ROOT / "data" / "documents"
    portal_base_url: str = "http://localhost:8100"
    agents: Literal["live", "stub"] = "live"  # stub: fixture-driven agents for demos and tests

    models_file: Path = REPO_ROOT / "models.yaml"


@lru_cache
def get_settings() -> Settings:
    return Settings()
