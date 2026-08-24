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

    # --- Fundamentals (存股 lane) ---
    # How many calendar years of annual EPS / ROE the history backfill asks
    # for. Ten is what the Earn dimension judges over; one more would be
    # fetched and never read.
    fundamentals_history_years: int = 11

    # FinMind supplies the annual history the exchanges do not publish. It is
    # a one-off data mover, not a runtime dependency: the backfill job writes
    # into `fundamentals_annual` and nothing user-facing calls it. Blank token
    # still works -- the datasets used are served anonymously -- but a
    # verified account doubles the hourly allowance and so halves how many
    # nights a full sweep takes.
    finmind_token: str = ""
    # Companies per backfill run, at two upstream requests each. 120 companies
    # is 240 requests, which fits inside the hourly budget below with room to
    # spare -- so a run finishes at network speed rather than sitting on a
    # limiter, and never provokes the quota refusal that would end it early.
    #
    # There are roughly 1,950 listed companies, so a daily run covers the
    # board in about seventeen nights. To finish sooner, move the job to
    # hourly at /admin/jobs for a day; to go faster still, set FINMIND_TOKEN
    # (a verified account doubles the ceiling to 600/hour) and raise
    # FINMIND_THROTTLE_MAX_CALLS with it.
    finmind_backfill_batch: int = 120
    # An hourly budget rather than a per-minute drip, because that is the
    # shape of the limit being respected: 300/hour anonymous, 600/hour with a
    # verified account. Set under the anonymous tier so the default
    # configuration works with no account at all.
    finmind_throttle_max_calls: int = 250
    finmind_throttle_window_seconds: float = 3600.0

    # --- Daily-history backfill (for the 存股 study) ---
    # Everything else fetches history lazily, so `daily_price` holds whatever
    # was browsed. That makes the hold backtest's window differ per stock and
    # makes "bucket by Chen score, measure what happened next" unrunnable --
    # the sample would be a record of clicking, not of the market.
    #
    # This job fills a defined universe instead: the companies with the longest
    # cash-payout record, which is the population the method is about.
    history_backfill_years: int = 10
    history_backfill_stocks: int = 300
    # Months fetched per run. The exchange limiter is shared with the realtime
    # poll, so this bounds how long one run can sit in front of a user waiting
    # for a quote. The job also refuses to run during market hours entirely.
    #
    # A *soft* cap, checked between stocks: a run finishes whichever name it is
    # on rather than leaving it half-fetched, so the real ceiling is
    # `budget + (years * 12 - 1)` months. At the defaults that is 250 + 119 =
    # 369 months, about eleven minutes at 3 requests / 5.5s. Sized for that
    # worst case, not for the nominal number.
    #
    # 300 stocks x 120 months is ~36,000 fetches, so hourly overnight covers
    # the universe in a handful of days. Watch `remaining` and move the job
    # back to nightly once it reaches zero.
    history_backfill_months_per_run: int = 250

    # --- AI analysis (Google Gemini) ---
    # Blank disables the feature outright: the endpoint answers 503 and the
    # frontend hides the button. There is no fallback model, because a service
    # that silently swaps in a different engine would invalidate every stored
    # verdict's `model` column without saying so.
    gemini_api_key: str = ""
    # First-boot / fallback default only. Once an admin picks a model at
    # /admin/ai the `system_setting` row wins, for the same reason job
    # schedules outlive their env seeds -- a UI change has to survive a
    # container restart, and an env var cannot be written back from a running
    # process. Pin a concrete id, never a `-latest` alias: the model string is
    # part of every stored verdict's cache key.
    gemini_model: str = "gemini-3.5-flash"
    # Closed set the admin UI may choose from. Deployments add or remove
    # entries here; the picker refuses anything outside this list so a typo
    # in the UI cannot point generation at a model the key cannot call.
    gemini_models: str = (
        "gemini-3.5-flash,gemini-3.6-flash,gemini-2.5-flash,"
        "gemini-2.5-flash-lite,gemini-2.5-pro"
    )
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

    # The deep lane's own ceiling. It is shown institutional flow and annual
    # figures on top of the price series and is asked to cite them, so its
    # answer is legitimately longer -- and a verdict truncated mid-JSON is a
    # failed request, not a shorter one.
    gemini_deep_max_output_tokens: int = 4096
    # Off for the same reason as above, and worth being explicit about: what
    # makes the deep lane deep is the extra data, not extra reasoning. Turning
    # this up is the fastest way to spend the output budget before the JSON
    # begins, so raise gemini_deep_max_output_tokens alongside it or not at all.
    gemini_deep_thinking_budget: int = 0

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

    @property
    def gemini_model_list(self) -> list[str]:
        """Ordered allowlist for the admin picker.

        `gemini_model` is always included even when omitted from
        `gemini_models`, so the fallback default is never an illegal choice.
        """
        models: list[str] = []
        seen: set[str] = set()
        for raw in self.gemini_models.split(","):
            name = raw.strip()
            if not name or name in seen:
                continue
            seen.add(name)
            models.append(name)
        default = self.gemini_model.strip()
        if default and default not in seen:
            models.insert(0, default)
        return models


@lru_cache
def get_settings() -> Settings:
    return Settings()
