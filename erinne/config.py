"""Settings, read from ERINNE_* environment variables and an optional .env file."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="ERINNE_", env_file=".env", extra="ignore")

    # Storage
    data_dir: Path = Path("data")

    # Web / sessions
    host: str = "127.0.0.1"
    port: int = 38200
    secret_key: str = ""
    session_max_age: int = 24 * 3600
    session_https_only: bool = False

    # Login throttling: lock a username+IP pair after this many failures.
    login_max_failures: int = 5
    login_lockout_seconds: int = 300

    # Usernames that are always admins (comma-separated); bootstrap only.
    admin_users: str = ""

    # Inference (llama-server)
    llm_url: str = "http://127.0.0.1:8081"
    embed_url: str = "http://127.0.0.1:8082"
    llm_timeout: float = 120.0

    # Embeddings: vector size fixes the chunk_vec table's shape.
    embed_model: str = "jina-embeddings-v2-base-en-q8_0"
    embed_dim: int = Field(default=768, gt=0)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "erinne.db"

    @property
    def blob_dir(self) -> Path:
        return self.data_dir / "blobs"

    @property
    def admin_usernames(self) -> set[str]:
        return {u.strip().lower() for u in self.admin_users.split(",") if u.strip()}


@lru_cache
def get_settings() -> Settings:
    return Settings()
