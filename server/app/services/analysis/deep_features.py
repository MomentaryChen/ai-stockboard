"""What a deep verdict is shown beyond the price series.

The technical lane's `features.py` exists to keep the model away from
arithmetic. This is the same job for the two stores the quick prompt is not
allowed to look at -- `chip_day` and `fundamentals_annual` -- and it obeys the
same three rules: pure function of rows already held, no session, no network,
no clock. A past date is replayed by slicing the inputs and nothing else.

Two things are worth stating because they are not obvious from the fields.

**Nets are turned into a share of turnover.** `chip_day` publishes 外資買超 in
shares, and a share count cannot be reasoned about without knowing how big the
stock trades: 8 000 000 shares is a takeover on one name and a quiet Tuesday on
2330. `net_5d_pct_of_volume` divides by the volume of the very sessions the net
was accumulated over, which is the form a citable reason takes.

**Coverage is an output, not an absence.** Every figure here can be missing for
a reason that is nobody's fault -- an ETF has no institutional report, a newly
listed company has not filed a full year, the nightly job has not reached this
name yet -- and "we did not look" must never reach the model looking like "we
looked and it was zero". `coverage_gaps`
carries stable slugs, the same contract `chen_rules` uses, and the prompt is
told to cap its confidence against them.
"""

from __future__ import annotations

import datetime

from app.models import ChipDay, DailyPrice, FundamentalsAnnual, ValuationDay
from app.schemas import (
    DeepChipColumn,
    DeepChipFeatures,
    DeepInputs,
    HoldFundamentalsFeatures,
)
from app.services import chip as chip_service
from app.services.analysis import hold_features

#: Sessions of chip history the deep prompt reads. Twenty is a trading month --
#: long enough for a 買超 run to be a position rather than a day, and short
#: enough that the nightly job's window covers it.
CHIP_WINDOW = 20

#: The short window every net is also reported over, matching `ChipFlow`'s so
#: the card and the prompt mean the same thing by "recent".
CHIP_SHORT_WINDOW = 5

#: Below this many covered sessions the chip read is called partial. Five is
#: the short window: fewer than that and `net_5d` is a sum of whatever happened
#: to be there, which is a different number from the one it claims to be.
CHIP_MIN_COVERAGE = 5

#: Years of EPS below which the earnings read is flagged short. Matches the
#: spirit of `chen_rules`: one or two years is a snapshot, not a record.
MIN_EPS_YEARS = 3

#: The four columns T86 publishes, in the order the prompt lists them.
_COLUMNS = ("foreign_net", "trust_net", "dealer_net", "total_net")


def _pct_of_volume(net: int | None, volume: int) -> float | None:
    """A net as a percentage of the shares traded in the same sessions.

    Signed, so a buy and a sell of the same size stay distinguishable. None
    when there was no turnover to divide by -- a suspended stock is not a stock
    with zero institutional interest.
    """
    if net is None or volume <= 0:
        return None
    return round(net / volume * 100, 2)


def _empty_column() -> DeepChipColumn:
    return DeepChipColumn(
        streak="none",
        streak_days=0,
        net_5d_shares=None,
        net_20d_shares=None,
        net_5d_pct_of_volume=None,
    )


def _column(values: list[int | None], short_volume: int) -> DeepChipColumn:
    """One institutional column, newest value first."""
    # `streak_and_net` already encodes what breaks a run (a zero and a hole are
    # neither a buy nor a sell) and that leading holes are skipped. Reusing it
    # rather than re-deriving means the streak on the 籌碼 card and the streak
    # in the prompt can never disagree.
    flow = chip_service.streak_and_net(values, window=CHIP_SHORT_WINDOW)

    long_sample = [v for v in values[:CHIP_WINDOW] if v is not None]
    net_20d = sum(long_sample) if long_sample else None

    return DeepChipColumn(
        streak=flow.streak,
        streak_days=flow.streak_days,
        net_5d_shares=flow.net_5d,
        net_20d_shares=net_20d,
        net_5d_pct_of_volume=_pct_of_volume(flow.net_5d, short_volume),
    )


def _latest(rows: list[ChipDay], attr: str) -> int | None:
    """Newest non-null value, or None if the column is empty throughout.

    `chip_day` is written by two independent reports, so a row can carry the
    institutional nets and not yet the margin balances. Falling through the
    holes is what keeps a balance from disappearing because the newest session
    is half-filled.
    """
    for row in rows:
        value = getattr(row, attr)
        if value is not None:
            return int(value)
    return None


def _sum(rows: list[ChipDay], attr: str) -> int | None:
    sample = [getattr(r, attr) for r in rows if getattr(r, attr) is not None]
    return sum(int(v) for v in sample) if sample else None


def _chip_features(chips: list[ChipDay], prices: list[DailyPrice]) -> DeepChipFeatures:
    """Institutional flow and margin over the last `CHIP_WINDOW` sessions."""
    # Newest first, which is the order `streak_and_net` counts a run in.
    rows = sorted(chips, key=lambda r: r.date, reverse=True)[:CHIP_WINDOW]

    if not rows:
        empty = _empty_column()
        return DeepChipFeatures(
            as_of=None,
            days_covered=0,
            foreign=empty,
            trust=empty,
            dealer=empty,
            total=empty,
            margin_balance=None,
            margin_change_5d=None,
            short_balance=None,
            short_change_5d=None,
        )

    # The denominator has to be the turnover of the same sessions the net was
    # accumulated over, not simply the last five bars in `daily_price`: a stock
    # whose chip coverage stops on Wednesday would otherwise be measured
    # against Friday's volume and read as a smaller position than it is.
    volume_by_date = {r.date: int(r.capacity) for r in prices if r.capacity is not None}
    short_dates = [r.date for r in rows[:CHIP_SHORT_WINDOW]]
    short_volume = sum(volume_by_date.get(d, 0) for d in short_dates)

    columns = {
        attr: _column([getattr(r, attr) for r in rows], short_volume)
        for attr in _COLUMNS
    }

    # Balances are levels, not flows, so the newest row carrying one wins --
    # unlike the nets, which are summed. The changes are summed over the short
    # window for the same reason a 5-day net is: one session's move says
    # nothing about whether leverage is building.
    return DeepChipFeatures(
        as_of=rows[0].date,
        days_covered=len(rows),
        foreign=columns["foreign_net"],
        trust=columns["trust_net"],
        dealer=columns["dealer_net"],
        total=columns["total_net"],
        margin_balance=_latest(rows, "margin_balance"),
        margin_change_5d=_sum(rows[:CHIP_SHORT_WINDOW], "margin_change"),
        short_balance=_latest(rows, "short_balance"),
        short_change_5d=_sum(rows[:CHIP_SHORT_WINDOW], "short_change"),
    )


def _coverage_gaps(
    chip: DeepChipFeatures, fundamentals: HoldFundamentalsFeatures
) -> list[str]:
    """What the deep read could not see, as stable identifiers.

    Slugs rather than sentences, so the UI renders them in the reader's own
    language -- the same contract `chen_rules._coverage_gaps` follows. It
    deliberately reuses that function's fundamentals slugs where the meaning is
    identical, so one catalogue serves both panels.
    """
    gaps: list[str] = []

    if chip.days_covered == 0:
        gaps.append("no_chip_data")
    elif chip.days_covered < CHIP_MIN_COVERAGE:
        gaps.append("partial_chip_coverage")
    if chip.days_covered > 0 and chip.margin_balance is None:
        # Distinct from having no chip at all: the institutional report landed
        # and the margin one did not, which is a normal state between the two
        # publications and a different thing for an operator to look at.
        gaps.append("no_margin_data")

    if fundamentals.years_available == 0:
        gaps.append("no_annual_fundamentals")
    else:
        if fundamentals.eps_years_checked < MIN_EPS_YEARS:
            gaps.append("short_eps_history")
        if fundamentals.trailing_pe is None:
            gaps.append("no_trailing_pe")

    return gaps


def extract(
    *,
    as_of: datetime.date,
    latest_close: float | None,
    prices: list[DailyPrice],
    chips: list[ChipDay],
    fundamentals: list[FundamentalsAnnual],
    valuation: ValuationDay | None = None,
) -> DeepInputs:
    """The deep prompt's extra inputs for one stock, plus what is missing.

    Never raises and never returns None. A stock with no chip rows and no
    annual figures still produces a structure -- one whose `coverage_gaps` say
    the deep read found nothing the quick one did not. That is a worse answer
    than a covered stock's, and it is an answer, which is the point: refusing
    to generate would leave the button dead on every ETF.
    """
    chip = _chip_features(chips, prices)
    annual = hold_features.fundamentals_features(
        fundamentals, valuation, as_of, latest_close
    )
    return DeepInputs(
        chip=chip,
        fundamentals=annual,
        coverage_gaps=_coverage_gaps(chip, annual),
    )
