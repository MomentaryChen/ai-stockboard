"""Application settings.

Loaded from `deployment/.env`, the same file Docker Compose reads, so the
database credentials only have to be written down once.
"""

import ipaddress
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

    # --- Signal backtest ---
    # How far back the 四大買賣點 replay reaches. One window is offered, not a
    # range the caller picks: a short window produces two or three signals, and
    # a win rate over three samples reads exactly as authoritative as one over
    # eighty. Twelve months is the shortest window that survives that objection
    # while still describing the regime the stock is currently in.
    backtest_window_months: int = 12

    # --- Logging ---
    log_level: str = "INFO"
    # text | json. JSON is one object per line for a log shipper; text is what
    # you want when the log is being read by a person in `docker compose logs`.
    log_format: str = "text"

    # --- Health / backup reporting ---
    # Path *inside this process* to the directory the db-backup container
    # writes dumps into. Distinct from the compose-side BACKUP_DIR, which is a
    # host path and means nothing in here. Unset -> /api/health reports the
    # backup as "unchecked" rather than missing, which is right for a server
    # run outside Docker.
    backup_status_dir: str = ""
    # A daily dump older than this is a fault worth reporting. 36h rather than
    # 24h so one late run, or the hour the clock changes, is not an alert.
    backup_max_age_hours: int = 36

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

    # --- Registration ---
    # New accounts land dormant and an ADMIN activates them by hand. The point
    # of gating realtime quotes behind a login is that the upstream quota is
    # attributable to somebody; open registration hands that quota to anyone
    # who can spend ten seconds on a signup form, which gives the gate nothing
    # to attribute. Turn it off for a local development database, not for a
    # deployment reachable from anywhere else.
    registration_requires_approval: bool = True

    # --- Login abuse ---
    # Two dimensions, because either one alone has an obvious way around it:
    # locking the account stops one attacker grinding one password list, and
    # throttling the source stops that same attacker spreading the attempts
    # across every username they can guess.
    #
    # Per account: consecutive failures before the account stops answering, and
    # how long it stays that way. Any success resets the counter.
    login_max_failures: int = 5
    login_lockout_minutes: int = 15
    # Per source IP: failures inside a sliding window. Deliberately far looser
    # than the per-account limit -- a whole office behind one NAT address shares
    # this budget, and locking a building out is a worse outcome than the extra
    # guesses this allows.
    login_ip_max_failures: int = 20
    login_ip_window_minutes: int = 5
    # Registrations from one source IP per hour. Cheap insurance against a
    # script filling the review queue faster than an admin can empty it.
    register_ip_max_per_hour: int = 5

    # Whose X-Real-IP header is worth believing. `deployment/docker-compose.yml`
    # publishes the API port on the host as well as putting nginx in front of
    # it, so the header is only trustworthy when the connection itself came
    # from the proxy -- anyone reaching :8000 directly can write whatever they
    # like in it. Blank means trust nobody and use the socket peer address.
    trusted_proxy_ips: str = "127.0.0.1,::1,10.0.0.0/8,172.16.0.0/12,192.168.0.0/16"

    # --- AI analysis (Google Gemini) ---
    # Blank disables the feature outright: the endpoint answers 503 and the
    # frontend hides the button. There is no fallback model, because a service
    # that silently swaps in a different engine would invalidate every stored
    # verdict's `model` column without saying so.
    gemini_api_key: str = ""
    gemini_model: str = "gemini-2.5-flash"
    # Generation is capped rather than sampled at 1.0: this is a position call,
    # and the same bars giving a different answer on each press would be read as
    # the market changing when it is only the sampler.
    gemini_temperature: float = 0.2
    gemini_max_output_tokens: int = 2048
    gemini_timeout_seconds: float = 45.0
    # Thinking tokens are billed against max_output_tokens, so a model left to
    # think freely can spend the whole budget before it starts the JSON and
    # return a truncated body -- a hard failure, not a worse answer. Off by
    # default because the reasoning here is shallow: roughly thirty
    # pre-computed numbers read against a rubric spelled out in the prompt.
    # Raise it (and max_output_tokens with it) to trade latency for depth.
    gemini_thinking_budget: int = 0

    # Same shape as the TWSE limiter, for the same reason: an upstream budget
    # the whole process shares. This one also costs money, so it is deliberately
    # tighter than Gemini's own quota would require.
    ai_throttle_max_calls: int = 5
    ai_throttle_window_seconds: float = 60.0

    # Generations one account may pay for per calendar day, in SCHEDULER_TIMEZONE.
    # A cache hit is free and never counted -- see services/analysis/ai.py.
    ai_daily_quota: int = 20
    ai_admin_daily_quota: int = 200

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def trusted_proxy_networks(self) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
        """Parsed once per settings instance; `get_settings` is lru_cached.

        A malformed entry is dropped rather than raising: a typo here must not
        stop the service from booting, and the failure mode of dropping it is
        the safe one -- that proxy simply stops being trusted.
        """
        networks = []
        for raw in self.trusted_proxy_ips.split(","):
            raw = raw.strip()
            if not raw:
                continue
            try:
                networks.append(ipaddress.ip_network(raw, strict=False))
            except ValueError:
                continue
        return tuple(networks)

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
