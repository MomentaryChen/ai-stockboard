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

    # --- Background jobs ---
    # Master switch for the scheduler thread pool. Off means no job ever fires
    # on its own; admins can still trigger them by hand from /admin/jobs. Use
    # it when running several replicas, so only one of them holds the jobs.
    jobs_scheduler_enabled: bool = True
    # Wall-clock timezone that "每天 03:00" is interpreted in. The container
    # sets TZ from the same .env, so the two agree by default.
    scheduler_timezone: str = "Asia/Taipei"

    # --- Listed-instrument table ---
    # `stock_code` is reconciled with the exchanges' ISIN registry on startup
    # and on the schedule below. Disabling the sync leaves whatever the table
    # already holds in place -- the API keeps working, it just stops learning
    # about new listings.
    #
    # These two are *first-boot defaults only*. Once an admin edits the
    # schedule at /admin/jobs the `job_schedule` row wins, because a change
    # made in the UI has to survive a restart and nothing in the process can
    # write back to an env var.
    stock_code_sync_enabled: bool = True
    stock_code_sync_interval_hours: int = 24

    # --- Auth / JWT ---
    # JWT_SECRET deliberately has no usable default: when it is blank,
    # app/security.py generates an ephemeral per-process secret and logs a
    # warning, so a misconfigured deployment is loud but never exploitable.
    jwt_secret: str = ""
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    refresh_token_expire_days: int = 7

    # First ADMIN account, seeded at startup when both email and password are
    # set. Leaving either blank skips seeding.
    admin_username: str = "admin"
    admin_email: str = ""
    admin_password: str = ""

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
