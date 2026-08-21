"""What background jobs exist, and what an operator may do to each of them.

The definitions here are the *closed set* of jobs this service runs. Everything
an admin can change -- on/off, cadence, the hour it fires -- is stored per job
in `job_schedule`; everything they must not change is a field of the definition
below and lives in code:

  * `min_interval_minutes` is a guard rail, not a preference. A five-minute
    listing sync would have us banned by TWSE inside a day (their limit is 3
    requests / 5 s and one run is an 8 MB scrape), so the API refuses it even
    from an administrator.
  * `manual_cooldown_seconds` bounds the damage of the run-now button --
    holding it down would otherwise be an outbound flood with an admin's name
    on it.
  * `stat_labels` is what lets one generic admin table render jobs that count
    completely different things: the keys are `job_run.stats` keys, the values
    are the column headers.

Adding a job means adding a definition here and a handler in `handlers.py`.
Nothing else -- the scheduler, the API and the UI are all driven off this list.

`name`, `description` and `stat_labels` are Traditional Chinese while the rest
of the server is English, and that is the CLAUDE.md rule rather than an
exception to it: they are the labels an operator reads on the admin console,
i.e. product copy, in the same way `services/analysis/traditional.py` returns
its 買賣點 reasons in Chinese. Errors and log lines here stay English.
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from app.config import get_settings
from app.models import SCHEDULE_DAILY, SCHEDULE_INTERVAL

settings = get_settings()

STOCK_CODE_SYNC = "stock_code_sync"
REFRESH_TOKEN_CLEANUP = "refresh_token_cleanup"
BACKTEST_REFRESH = "backtest_refresh"
CHIP_REFRESH = "chip_refresh"


@dataclass(frozen=True)
class JobResult:
    """What one attempt did. Returned by a handler, stored as a `job_run` row.

    `skipped` is not a failure and not a success: it means the job woke up and
    correctly found nothing to do. Recording it is the point -- it is the
    heartbeat that separates "the scheduler is alive and idle" from "the
    scheduler died a fortnight ago", which are otherwise identical.
    """

    status: str  # success | skipped | failed
    message: str | None = None
    stats: dict[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class JobContext:
    """Everything a handler is given. Handlers take no other arguments."""

    db: object  # sqlalchemy Session -- typed loosely to keep this module import-light
    trigger: str  # startup | schedule | manual
    # True only for the manual run-now button: the operator is explicitly
    # asking for the work to happen whether or not it looks necessary.
    force: bool
    # The configured cadence, so a handler that can cheaply tell it has nothing
    # to do (the listing sync reads `stock_code.synced_at`) can skip without
    # the scheduler having to know how.
    freshness_seconds: float


@dataclass(frozen=True)
class JobDefinition:
    id: str
    name: str  # shown in the admin UI
    description: str
    handler: Callable[[JobContext], JobResult]

    # --- first-boot defaults; a `job_schedule` row overrides all of these ---
    default_enabled: bool
    default_kind: str
    default_interval_minutes: int
    default_daily_at: str  # "HH:MM"

    # --- guard rails an admin cannot cross ---
    min_interval_minutes: int
    max_interval_minutes: int
    manual_cooldown_seconds: int

    # Fire once at startup regardless of the schedule. Only for jobs whose
    # handler can tell by itself that there is nothing to do, or the container
    # restarting in a crash loop turns into a scrape loop.
    run_on_startup: bool
    expected_seconds: int  # what the UI tells the operator to expect
    stat_labels: dict[str, str]


_DEFINITIONS: dict[str, JobDefinition] = {}
_built = False


def register(definition: JobDefinition) -> JobDefinition:
    _DEFINITIONS[definition.id] = definition
    return definition


def all_jobs() -> list[JobDefinition]:
    """Every job, in the order the admin UI lists them."""
    build()
    return list(_DEFINITIONS.values())


def get(job_id: str) -> JobDefinition | None:
    build()
    return _DEFINITIONS.get(job_id)


def build() -> None:
    """Populate the registry, once.

    Idempotent and called from every entry point rather than at import time,
    so a test that imports the router gets a populated registry without having
    to run the app's lifespan first.
    """
    global _built
    if _built:
        return
    _built = True

    # Imported here rather than at module scope: handlers import this module
    # for JobResult/JobContext, so the dependency has to run one way only.
    from app.services.jobs import handlers

    register(
        JobDefinition(
            id=STOCK_CODE_SYNC,
            name="上市櫃名冊同步",
            description=(
                "向交易所 ISIN 名冊重抓上市／上櫃代碼，寫入 stock_code。"
                "沒有它，新掛牌的標的一律查無此股。"
            ),
            handler=handlers.stock_code_sync,
            # STOCK_CODE_SYNC_ENABLED / _INTERVAL_HOURS seed the first boot;
            # after that the row an admin saved wins.
            default_enabled=settings.stock_code_sync_enabled,
            default_kind=SCHEDULE_INTERVAL,
            default_interval_minutes=max(settings.stock_code_sync_interval_hours, 1) * 60,
            default_daily_at="03:00",
            # One run is two multi-megabyte scrapes off a shared rate limiter.
            # Hourly is already generous; anything under that is a ban.
            min_interval_minutes=60,
            max_interval_minutes=14 * 24 * 60,
            manual_cooldown_seconds=300,
            run_on_startup=True,
            expected_seconds=45,
            stat_labels={
                "inserted": "新增",
                "updated": "更新",
                "delisted": "下市",
                "pruned": "清除",
                "active": "可查詢",
            },
        )
    )

    register(
        JobDefinition(
            id=REFRESH_TOKEN_CLEANUP,
            name="登入憑證清理",
            description=(
                "刪除已過期、以及撤銷超過保留期的 refresh token。"
                "保留期內的撤銷紀錄要留著，重放偵測才抓得到被偷的憑證。"
            ),
            handler=handlers.refresh_token_cleanup,
            default_enabled=True,
            default_kind=SCHEDULE_DAILY,
            default_interval_minutes=24 * 60,
            default_daily_at="04:10",
            min_interval_minutes=5,
            max_interval_minutes=7 * 24 * 60,
            manual_cooldown_seconds=10,
            # Cheap, but nothing forces it: one delete at boot buys nothing that
            # the scheduled run will not do at 04:10 anyway.
            run_on_startup=False,
            expected_seconds=1,
            stat_labels={"deleted": "刪除"},
        )
    )

    register(
        JobDefinition(
            id=BACKTEST_REFRESH,
            name="訊號回測預算",
            description=(
                "把每檔已有日線的股票重跑一次四大買賣點回測，寫入 backtest_result。"
                "只讀本地日線、不連交易所；沒有它，卡片第一次開啟要現算。"
            ),
            handler=handlers.backtest_refresh,
            default_enabled=True,
            default_kind=SCHEDULE_DAILY,
            # After the daily-price jobs have had the night to land new bars.
            # Running it before them would recompute every stock against
            # yesterday's close and mark the result fresh.
            default_daily_at="05:20",
            default_interval_minutes=24 * 60,
            # Pure CPU over local rows -- no exchange budget to protect, so the
            # floor only stops a misconfiguration from pinning a core.
            min_interval_minutes=30,
            max_interval_minutes=7 * 24 * 60,
            manual_cooldown_seconds=30,
            # Every row is derived and the endpoint recomputes anything missing
            # on demand, so a cold start costs a slower first card, not a wrong
            # one -- which is not worth a replay of the whole table at boot.
            run_on_startup=False,
            expected_seconds=20,
            stat_labels={
                "computed": "重算",
                "skipped": "略過",
                "failed": "失敗",
            },
        )
    )

    register(
        JobDefinition(
            id=CHIP_REFRESH,
            name="籌碼日報快取",
            description=(
                "把最近幾個交易日的三大法人與融資融券日報抓進 chip_day。"
                "一份日報涵蓋全市場，個股頁共用；沒有它，第一次打開要現抓。"
            ),
            handler=handlers.chip_refresh,
            default_enabled=True,
            default_kind=SCHEDULE_DAILY,
            # T86 and MI_MARGN land after the close, typically 18:00–19:40.
            # Later than that and the card's "as of yesterday" is a cache miss
            # until someone opens a stock; earlier and we stamp an empty "not
            # published yet" that then has to expire.
            default_daily_at="20:30",
            default_interval_minutes=24 * 60,
            min_interval_minutes=60,
            max_interval_minutes=7 * 24 * 60,
            manual_cooldown_seconds=60,
            run_on_startup=False,
            expected_seconds=45,
            stat_labels={
                "fetched": "新抓日數",
                "cached": "已快取",
                "rows": "寫入列數",
            },
        )
    )
