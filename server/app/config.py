"""Application settings.

Loaded from `deployment/.env`, the same file Docker Compose reads, so the
database credentials only have to be written down once.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# <project root>/deployment/.env  (this file lives at <root>/server/app/config.py)
ENV_FILE = Path(__file__).resolve().parents[2] / "deployment" / ".env"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ENV_FILE, env_file_encoding="utf-8", extra="ignore"
    )

    # --- PostgreSQL: same variables the db container is configured with ---
    postgres_user: str = "stockboard"
    postgres_password: str = "stockboard"
    postgres_db: str = "stockboard"
    postgres_host: str = "localhost"
    postgres_port: int = 5433

    # Optional override, e.g. to point at a managed database. When unset the URL
    # is assembled from the POSTGRES_* values above.
    database_url: str | None = None

    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"

    # The current month keeps changing, so its cached copy expires.
    # Past months are immutable and are never re-fetched.
    current_month_ttl_seconds: int = 900

    # TWSE bans clients doing more than 3 requests per 5 seconds.
    throttle_max_calls: int = 3
    throttle_window_seconds: float = 5.5

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
