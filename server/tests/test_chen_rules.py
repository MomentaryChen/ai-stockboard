"""Pins the checklist's shape, not its thresholds.

The numbers in `chen_rules` are meant to be retuned -- that is the point of
having a deterministic lane beside a model. What must not change without
somebody meaning it is the structure around them:

  * `unknown` shrinks the denominator instead of counting as a failure. Every
    stock on the board has no fundamentals today, and scoring that as five
    failures would report the whole market as unsuitable and then "improve" it
    for reasons that have nothing to do with the companies;
  * nothing known means no score at all, not zero;
  * a thinly covered name cannot be labelled `strong`, however well it did on
    the two dimensions we could check;
  * `coverage_gaps` stays machine-readable, because the UI renders it in two
    languages from its own catalogue.

Threshold *behaviour* is asserted at the boundary rather than by value, so
retuning a constant moves these tests with it instead of breaking them.
"""

from __future__ import annotations

import datetime

from app.schemas import (
    HoldDividendFeatures,
    HoldFeatures,
    HoldFundamentalsFeatures,
    HoldLiquidityFeatures,
    HoldPriceFeatures,
)
from app.services.analysis import chen_rules


def _features(
    *,
    industry: str = "半導體業",
    # dividends
    coverage: str = "history",
    years_observed: int = 10,
    years_with_cash: int = 0,
    consecutive_years: int = 0,
    cash_yield_pct: float | None = None,
    # liquidity
    avg_daily_shares: int | None = None,
    no_trade_days: int = 0,
    trading_days: int = 60,
    # fundamentals
    eps_years: int = 0,
    eps_positive: int | None = None,
    avg_roe_pct: float | None = None,
    roe_stdev_pct: float | None = None,
    roe_years: int = 0,
    trailing_pe: float | None = None,
    cape: float | None = None,
    latest_eps: float | None = None,
) -> HoldFeatures:
    """A snapshot with every dimension unknown, and knobs to make one known."""
    return HoldFeatures(
        sid="2330",
        name="test",
        as_of=datetime.date(2026, 3, 20),
        industry=industry,
        dividend=HoldDividendFeatures(
            coverage=coverage,
            window_years=10,
            years_observed=years_observed,
            years_with_cash=years_with_cash,
            consecutive_years_with_cash=consecutive_years,
            ttm_cash=None,
            cash_yield_pct=cash_yield_pct,
            avg_cash_per_year=None,
            avg_yield_pct=None,
            latest_ex_date=None,
            years_with_stock_dividend=0,
        ),
        liquidity=HoldLiquidityFeatures(
            trading_days=trading_days,
            avg_daily_shares=avg_daily_shares,
            avg_daily_turnover=None,
            no_trade_days=no_trade_days,
        ),
        fundamentals=HoldFundamentalsFeatures(
            years_available=eps_years,
            eps_years_checked=eps_years,
            eps_positive_years=eps_positive,
            latest_eps=latest_eps,
            latest_eps_year=2025 if eps_years else None,
            avg_eps=None,
            avg_roe_pct=avg_roe_pct,
            roe_stdev_pct=roe_stdev_pct,
            roe_years_checked=roe_years,
            trailing_pe=trailing_pe,
            cape=cape,
            cape_years=10 if cape is not None else 0,
        ),
        price=HoldPriceFeatures(
            latest_close=100.0,
            window_high=None,
            window_low=None,
            position_pct=None,
            drawdown_from_high_pct=None,
            return_1y_pct=None,
        ),
    )


def _by_key(result) -> dict[str, str]:
    return {d.key: d.status for d in result.dimensions}


# --- coverage -----------------------------------------------------------------


def test_the_shipping_state_scores_nothing_rather_than_scoring_zero():
    """No fundamentals, no dividends, no bars -- which is every stock on day one."""
    result = chen_rules.evaluate(
        _features(trading_days=0, coverage="recent")
    )

    assert result.score is None
    assert result.suitability is None
    assert result.known_weight == 0
    assert set(_by_key(result).values()) == {"unknown"}


def test_unknown_dimensions_leave_the_denominator_rather_than_failing():
    """A name we only have dividends and volume for is judged on those two."""
    result = chen_rules.evaluate(
        _features(
            years_with_cash=10,
            consecutive_years=10,
            cash_yield_pct=6.0,
            avg_daily_shares=chen_rules.LIQUID_MIN_AVG_SHARES * 2,
        )
    )

    statuses = _by_key(result)
    assert statuses["collect"] == "pass"
    assert statuses["liquid"] == "pass"
    assert statuses["earn"] == statuses["efficient"] == statuses["cheap"] == "unknown"

    expected = chen_rules.WEIGHTS["collect"] + chen_rules.WEIGHTS["liquid"]
    assert result.known_weight == expected
    assert result.total_weight == chen_rules.TOTAL_WEIGHT
    # Both known dimensions passed, so the score is full over what was checked.
    assert result.score == 100


def test_a_thinly_covered_name_cannot_be_called_strong():
    """The score is honest about what it checked; the label must be too."""
    result = chen_rules.evaluate(
        _features(
            years_with_cash=10,
            consecutive_years=10,
            cash_yield_pct=6.0,
            avg_daily_shares=chen_rules.LIQUID_MIN_AVG_SHARES * 2,
        )
    )
    assert result.score == 100
    assert result.known_weight < chen_rules.MIN_KNOWN_WEIGHT_FOR_STRONG
    assert result.suitability == "ok"


def test_missing_fundamentals_are_reported_once_not_three_times():
    """Three unknown dimensions, one thing for an operator to do about them."""
    result = chen_rules.evaluate(_features())
    assert result.coverage_gaps.count("no_annual_fundamentals") == 1
    assert "short_eps_history" not in result.coverage_gaps


def test_coverage_gaps_are_identifiers_rather_than_sentences():
    """The UI renders them in two languages; a server sentence cannot be."""
    result = chen_rules.evaluate(_features(trading_days=0, coverage="recent"))
    for gap in result.coverage_gaps:
        assert gap.islower()
        assert " " not in gap


# --- Collect ------------------------------------------------------------------


def test_an_empty_tpex_record_is_unknown_rather_than_a_company_that_pays_nothing():
    """TPEX publishes a recent window only. Absent data is not evidence."""
    result = chen_rules.evaluate(_features(coverage="recent", years_with_cash=0))
    assert _by_key(result)["collect"] == "unknown"
    assert "tpex_recent_dividends_only" in result.coverage_gaps


def test_a_twse_name_that_never_paid_fails_rather_than_going_unknown():
    """Here the archive *is* the evidence, and the answer is no."""
    result = chen_rules.evaluate(_features(coverage="history", years_with_cash=0))
    assert _by_key(result)["collect"] == "fail"


def test_a_fat_yield_on_a_short_record_does_not_pass():
    """Usually a price that has fallen for a reason, not a payer to accumulate."""
    result = chen_rules.evaluate(
        _features(
            years_with_cash=3,
            consecutive_years=3,
            cash_yield_pct=chen_rules.COLLECT_MIN_YIELD_PCT * 2,
        )
    )
    assert _by_key(result)["collect"] == "fail"


def test_a_streak_bounded_by_our_own_fetch_window_is_unknown_not_a_failure():
    """The bug this rule exists for.

    `coverage` says TWSE publishes a yearly archive; it does not say we pulled
    one. On a fresh database the dividend card has fetched about five years, so
    a company paying every one of them shows a five-year streak -- and failing
    it for not reaching eight would be failing it for our gap, not its own.
    """
    thin = chen_rules.evaluate(
        _features(
            years_observed=4,
            years_with_cash=4,
            consecutive_years=4,
            cash_yield_pct=chen_rules.COLLECT_MIN_YIELD_PCT + 1,
        )
    )
    assert _by_key(thin)["collect"] == "unknown"
    assert "dividend_archive_not_warmed" in thin.coverage_gaps

    # The same company once the warmup has pulled the full archive: now the
    # four-year streak sits inside ten observed years, so the gap is real.
    warmed = chen_rules.evaluate(
        _features(
            years_observed=10,
            years_with_cash=4,
            consecutive_years=4,
            cash_yield_pct=chen_rules.COLLECT_MIN_YIELD_PCT + 1,
        )
    )
    assert _by_key(warmed)["collect"] == "fail"


def test_a_short_archive_still_passes_a_streak_that_already_clears_the_bar():
    """A floor is still a verdict when the floor is high enough.

    Eight unbroken years is eight unbroken years, whether or not a ninth was
    ever fetched -- so this must not be downgraded to unknown along with the
    genuinely undecidable cases.
    """
    streak = chen_rules.COLLECT_STRONG_STREAK_YEARS
    result = chen_rules.evaluate(
        _features(years_observed=streak, years_with_cash=streak, consecutive_years=streak)
    )
    assert _by_key(result)["collect"] == "pass"


def test_an_observed_gap_is_a_real_failure_however_short_the_archive():
    """We watched the year in between and nothing was paid. That is evidence."""
    result = chen_rules.evaluate(
        _features(years_observed=5, years_with_cash=3, consecutive_years=2)
    )
    assert _by_key(result)["collect"] == "fail"

def test_a_long_unbroken_record_passes_without_needing_the_yield():
    result = chen_rules.evaluate(
        _features(
            years_with_cash=chen_rules.COLLECT_STRONG_STREAK_YEARS,
            consecutive_years=chen_rules.COLLECT_STRONG_STREAK_YEARS,
            cash_yield_pct=1.0,
        )
    )
    assert _by_key(result)["collect"] == "pass"


# --- Earn / Efficient / Cheap -------------------------------------------------


def test_earn_tolerates_a_macro_loss_year_but_not_a_pattern_of_them():
    years = chen_rules.EARN_MIN_YEARS + 3
    tolerable = chen_rules.EARN_MAX_LOSS_YEARS

    ok = chen_rules.evaluate(
        _features(eps_years=years, eps_positive=years - tolerable)
    )
    assert _by_key(ok)["earn"] == "pass"

    not_ok = chen_rules.evaluate(
        _features(eps_years=years, eps_positive=years - tolerable - 1)
    )
    assert _by_key(not_ok)["earn"] == "fail"


def test_earn_stays_unknown_on_a_record_too_short_to_mean_anything():
    short = chen_rules.EARN_MIN_YEARS - 1
    result = chen_rules.evaluate(_features(eps_years=short, eps_positive=short))
    assert _by_key(result)["earn"] == "unknown"
    assert "short_eps_history" in result.coverage_gaps


def test_a_high_average_roe_built_from_one_spike_is_not_efficient():
    stable = chen_rules.evaluate(
        _features(
            avg_roe_pct=chen_rules.EFFICIENT_MIN_AVG_ROE_PCT + 2,
            roe_stdev_pct=chen_rules.EFFICIENT_MAX_ROE_STDEV_PCT - 1,
            roe_years=5,
        )
    )
    assert _by_key(stable)["efficient"] == "pass"

    spiky = chen_rules.evaluate(
        _features(
            avg_roe_pct=chen_rules.EFFICIENT_MIN_AVG_ROE_PCT + 2,
            roe_stdev_pct=chen_rules.EFFICIENT_MAX_ROE_STDEV_PCT + 1,
            roe_years=5,
        )
    )
    assert _by_key(spiky)["efficient"] == "fail"


def test_financials_are_priced_against_a_tighter_band_than_everything_else():
    """A bank at 12x and a manufacturer at 12x are not the same statement."""
    assert chen_rules.pe_ceiling("金融保險業") == chen_rules.CHEAP_MAX_PE_FINANCIAL
    assert chen_rules.pe_ceiling("半導體業") == chen_rules.CHEAP_MAX_PE

    between = (chen_rules.CHEAP_MAX_PE + chen_rules.CHEAP_MAX_PE_FINANCIAL) / 2
    bank = chen_rules.evaluate(_features(industry="金融保險業", trailing_pe=between))
    chip = chen_rules.evaluate(_features(industry="半導體業", trailing_pe=between))

    assert _by_key(bank)["cheap"] == "fail"
    assert _by_key(chip)["cheap"] == "pass"


def test_a_loss_making_company_has_no_pe_rather_than_a_cheap_one():
    """A negative PE is not a bargain, and must not be scored as one."""
    result = chen_rules.evaluate(
        _features(eps_years=6, eps_positive=3, latest_eps=-1.0, trailing_pe=None)
    )
    assert _by_key(result)["cheap"] == "unknown"
    assert _by_key(result)["earn"] == "fail"
    assert "no_trailing_pe" in result.coverage_gaps


# --- Liquid -------------------------------------------------------------------


def test_a_name_that_often_does_not_trade_fails_whatever_its_average_says():
    busy = chen_rules.LIQUID_MIN_AVG_SHARES * 10
    total = 60
    quiet = int(total * chen_rules.LIQUID_MAX_NO_TRADE_RATIO) + 5

    result = chen_rules.evaluate(
        _features(avg_daily_shares=busy, trading_days=total, no_trade_days=quiet)
    )
    assert _by_key(result)["liquid"] == "fail"


# --- the summary handed to the prompt -----------------------------------------


def test_the_prompt_summary_names_every_dimension_and_its_evidence():
    """A model told only the score cannot disagree with the reasoning."""
    result = chen_rules.evaluate(
        _features(
            years_with_cash=10,
            consecutive_years=10,
            cash_yield_pct=6.0,
            avg_daily_shares=chen_rules.LIQUID_MIN_AVG_SHARES * 2,
        )
    )
    text = chen_rules.summarise(result)

    for key in chen_rules.WEIGHTS:
        assert key in text
    assert "coverage gaps" in text
    for dimension in result.dimensions:
        assert dimension.evidence in text


def test_an_unscored_checklist_says_so_rather_than_reporting_none_out_of_100():
    nothing_known = _features(trading_days=0, coverage="recent")
    text = chen_rules.summarise(chen_rules.evaluate(nothing_known))
    assert "not scored" in text
    assert "None" not in text


def test_an_unchecked_dividend_record_cannot_be_labelled_strong():
    """The defect this cap exists for.

    An OTC name has no yearly dividend archive, so Collect is structurally
    `unknown` -- leaving 75 of 100 weight known, comfortably past the thin
    threshold. Before the cap, such a stock could pass the other four and be
    labelled 「適合存股」 while nobody knew whether it had ever paid a dividend.
    """
    otc = chen_rules.evaluate(
        _features(
            coverage="recent",           # TPEx: Collect goes unknown
            years_with_cash=0,
            eps_years=8, eps_positive=8,
            avg_roe_pct=chen_rules.EFFICIENT_MIN_AVG_ROE_PCT + 5,
            roe_stdev_pct=1.0, roe_years=8,
            trailing_pe=chen_rules.CHEAP_MAX_PE - 3,
            avg_daily_shares=chen_rules.LIQUID_MIN_AVG_SHARES * 3,
        )
    )

    statuses = _by_key(otc)
    assert statuses["collect"] == "unknown"
    assert all(statuses[k] == "pass" for k in ("earn", "efficient", "cheap", "liquid"))

    # Everything that *was* checked passed, and the score says so...
    assert otc.score == 100
    # ...but the weight alone would have cleared the old gate.
    assert otc.known_weight > chen_rules.MIN_KNOWN_WEIGHT_FOR_STRONG
    # ...and the label still refuses, because the premise is unverified.
    assert otc.suitability == "ok"


def test_the_same_stock_is_strong_once_its_payout_record_is_known():
    """The cap is about the missing check, not about the stock."""
    covered = chen_rules.evaluate(
        _features(
            coverage="history",
            years_observed=10,
            years_with_cash=10,
            consecutive_years=10,
            cash_yield_pct=chen_rules.COLLECT_MIN_YIELD_PCT + 1,
            eps_years=8, eps_positive=8,
            avg_roe_pct=chen_rules.EFFICIENT_MIN_AVG_ROE_PCT + 5,
            roe_stdev_pct=1.0, roe_years=8,
            trailing_pe=chen_rules.CHEAP_MAX_PE - 3,
            avg_daily_shares=chen_rules.LIQUID_MIN_AVG_SHARES * 3,
        )
    )
    assert _by_key(covered)["collect"] == "pass"
    assert covered.suitability == "strong"


def test_a_cap_never_moves_a_label_up_or_touches_the_score():
    """Both caps are one-directional; the number stays what it measured."""
    weak = chen_rules.evaluate(
        _features(
            coverage="recent",
            years_with_cash=0,
            eps_years=8, eps_positive=2,
            avg_daily_shares=1,
        )
    )
    assert weak.suitability in ("weak", "avoid")
    assert weak.score is not None and weak.score < chen_rules.BAND_OK


def test_a_cyclical_at_its_earnings_peak_does_not_read_as_cheap():
    """The value trap a trailing PE walks straight into.

    A steel or shipping name at the top of its cycle earns a record year, so
    the denominator peaks with it and the trailing PE prints single digits.
    Averaging earnings over a decade is what says otherwise.
    """
    ceiling = chen_rules.CHEAP_MAX_PE          # general industry
    cape_ceiling = ceiling * chen_rules.CHEAP_CAPE_TOLERANCE

    trap = chen_rules.evaluate(
        _features(
            industry="鋼鐵工業",
            trailing_pe=ceiling / 2,           # looks like a bargain...
            cape=cape_ceiling * 1.5,           # ...on one exceptional year
        )
    )
    assert _by_key(trap)["cheap"] == "fail"
    assert "cyclically elevated" in next(
        d.evidence for d in trap.dimensions if d.key == "cheap"
    )

    # A steadily-earning company at the same trailing PE still passes.
    steady = chen_rules.evaluate(
        _features(
            industry="鋼鐵工業",
            trailing_pe=ceiling / 2,
            cape=ceiling / 2 * 1.1,
        )
    )
    assert _by_key(steady)["cheap"] == "pass"


def test_without_enough_eps_history_cheap_falls_back_to_the_trailing_check():
    """Saying so in the evidence, rather than silently checking one thing."""
    result = chen_rules.evaluate(
        _features(trailing_pe=chen_rules.CHEAP_MAX_PE - 1, cape=None)
    )
    assert _by_key(result)["cheap"] == "pass"
    assert "too few years" in next(
        d.evidence for d in result.dimensions if d.key == "cheap"
    )
