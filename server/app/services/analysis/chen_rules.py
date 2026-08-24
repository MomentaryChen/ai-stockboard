"""陳重銘存股術, encoded as a checklist that can be scored without a model.

The public method is five questions: does the company earn every year, does it
use its capital efficiently, is the price cheap, does it pay reliably, and can
you actually accumulate it. This module answers those five from
`hold_features.HoldFeatures` and nothing else -- no session, no clock, no
network -- which is what makes it free to run, replayable over a past snapshot,
and comparable against the AI verdict beside it rather than blended into one.

Three properties are load-bearing.

**`unknown` is not `fail`.** Until the fundamentals ingest lands, Earn /
Efficient / Cheap have no data at all. Scoring them as failures would report
every stock on the board as unsuitable and would make the number move when the
ingest arrives for reasons that have nothing to do with the companies. Unknown
dimensions leave both sides of the fraction instead, and their absence is
reported as `known_weight` and in `coverage_gaps`.

**The thresholds are named constants, not literals in an `if`.** Retuning them
is the point of having a deterministic lane at all -- it is the half of the
comparison that can be changed without a prompt edit and re-measured.

**The qualitative half of the method is absent on purpose.** 護城河 and 能傳
are the parts of Chen's framing that need judgement rather than arithmetic;
they belong to the narrative model next door, and a pass/fail here would be an
invented number wearing a checklist's authority.
"""

from __future__ import annotations

from app.schemas import (
    ChenDimension,
    ChenRuleResult,
    ChenStatus,
    HoldFeatures,
    HoldSuitability,
)

# --- thresholds ---------------------------------------------------------------
#
# Defaults, all tunable. They encode the published heuristics rather than any
# claim to optimality; the hold backtest is what will eventually argue with
# them.

#: Minimum years of EPS data before Earn is scored at all. Under this the
#: sample says more about how long the company has been listed than about
#: whether it earns reliably.
EARN_MIN_YEARS = 5
#: Loss years tolerated inside the window. One, not zero: an otherwise
#: consistent payer that lost money in 2008 or 2020 is exactly the kind of name
#: the method buys, and a zero-tolerance rule would reject the whole board on a
#: single macro year.
EARN_MAX_LOSS_YEARS = 1

#: Average ROE the method calls efficient. 10% is the conventional Taiwanese
#: retail benchmark and is what Chen's peer comparisons are pitched around.
EFFICIENT_MIN_AVG_ROE_PCT = 10.0
#: A high average built from one spike is not "efficient" in the sense meant
#: here, so an unstable series fails even when the mean clears the bar.
EFFICIENT_MAX_ROE_STDEV_PCT = 8.0
EFFICIENT_MIN_YEARS = 3

#: Trailing PE bands. Financials get the tighter one because the method's own
#: examples are financial holdings and it prices them around 10x; everything
#: else is judged against the broader market band.
CHEAP_MAX_PE = 15.0
CHEAP_MAX_PE_FINANCIAL = 10.0

#: The cyclically adjusted PE is judged against the same ceiling widened by
#: this much. Average earnings are lower than peak earnings for anything
#: cyclical, so a CAPE is structurally higher than a trailing PE and holding
#: both to one number would fail every cyclical outright -- which is not the
#: point. The point is to catch the name whose trailing PE is single-digit
#: *only* because this year was the top of its cycle.
CHEAP_CAPE_TOLERANCE = 1.4
#: 產業別 strings the ISIN listing uses for financial holdings and insurers.
#: Matched as substrings because the registry has several variants and adding
#: a sector should not require a code change to keep the others working.
FINANCIAL_INDUSTRY_MARKERS = ("金融", "保險", "銀行", "證券")

#: Cash yield the method treats as worth collecting.
COLLECT_MIN_YIELD_PCT = 5.0
#: ...but yield alone is a trap: a collapsing price lifts it. Continuity is the
#: other half, and either a long unbroken streak or a decent yield backed by a
#: shorter one passes.
COLLECT_MIN_STREAK_YEARS = 5
COLLECT_STRONG_STREAK_YEARS = 8
#: Below this the dividend record is too short to judge either way.
COLLECT_MIN_YEARS_OBSERVED = 3

#: Shares per session, averaged over the liquidity window. 500 張 a day is
#: roughly where a retail holder can add a few 張 a month without moving the
#: price against themselves.
LIQUID_MIN_AVG_SHARES = 500_000
#: A name that goes untraded this often cannot be accumulated on a schedule,
#: whatever its average says.
LIQUID_MAX_NO_TRADE_RATIO = 0.1

#: Weights, summing to 100. Earn and Collect carry the most because they are
#: the two the method will not compromise on; Liquid is a gate rather than a
#: virtue, so it is worth the least.
WEIGHTS: dict[str, int] = {
    "earn": 25,
    "efficient": 20,
    "cheap": 20,
    "collect": 25,
    "liquid": 10,
}
TOTAL_WEIGHT = sum(WEIGHTS.values())

#: Score bands.
BAND_STRONG = 80
BAND_OK = 60
BAND_WEAK = 40

#: Below this much known weight the score is real but thin, and calling a name
#: `strong` off two of five dimensions would overstate what was checked. The
#: score itself is left alone -- only the label is capped -- so the number and
#: the word never disagree about the evidence.
MIN_KNOWN_WEIGHT_FOR_STRONG = 60

#: Dimensions the method cannot be applied without, whatever the rest score.
#:
#: Weight alone is the wrong gate here, and shipping it that way was a real
#: defect: an OTC name has no dividend archive to read, so Collect is
#: structurally `unknown`, leaving 75 of 100 weight known -- comfortably over
#: the threshold above. A stock could therefore be labelled `strong` while
#: nobody knew whether it had ever paid a dividend, which for a
#: dividend-accumulation method is the one thing that cannot be left open.
#:
#: Missing Liquid (weight 10) and missing Collect (weight 25, and the premise
#: of the whole method) are not interchangeable, and a weighted denominator
#: cannot express that difference.
ESSENTIAL_DIMENSIONS = ("collect",)


def is_financial(industry: str) -> bool:
    return any(marker in industry for marker in FINANCIAL_INDUSTRY_MARKERS)


def pe_ceiling(industry: str) -> float:
    return CHEAP_MAX_PE_FINANCIAL if is_financial(industry) else CHEAP_MAX_PE


def _dimension(
    key: str,
    status: ChenStatus,
    evidence: str,
    metrics: dict[str, float | None],
) -> ChenDimension:
    return ChenDimension(
        key=key,
        status=status,
        weight=WEIGHTS[key],
        evidence=evidence,
        # Nulls are dropped rather than serialised: the card renders a metric
        # it is given, and "the value is missing" is already carried by the
        # status.
        metrics={k: v for k, v in metrics.items() if v is not None},
    )


def _earn(features: HoldFeatures) -> ChenDimension:
    f = features.fundamentals
    metrics = {
        "years_checked": float(f.eps_years_checked),
        "positive_years": (
            float(f.eps_positive_years) if f.eps_positive_years is not None else None
        ),
        "latest_eps": f.latest_eps,
        "avg_eps": f.avg_eps,
    }

    if f.eps_years_checked < EARN_MIN_YEARS or f.eps_positive_years is None:
        return _dimension(
            "earn",
            "unknown",
            f"only {f.eps_years_checked} year(s) of annual EPS stored; "
            f"{EARN_MIN_YEARS} needed",
            metrics,
        )

    losses = f.eps_years_checked - f.eps_positive_years
    passed = losses <= EARN_MAX_LOSS_YEARS
    return _dimension(
        "earn",
        "pass" if passed else "fail",
        f"{f.eps_positive_years}/{f.eps_years_checked} years with positive EPS "
        f"({losses} loss year(s); at most {EARN_MAX_LOSS_YEARS} tolerated)",
        metrics,
    )


def _efficient(features: HoldFeatures) -> ChenDimension:
    f = features.fundamentals
    metrics = {
        "avg_roe_pct": f.avg_roe_pct,
        "roe_stdev_pct": f.roe_stdev_pct,
        "years_checked": float(f.roe_years_checked),
        "threshold_pct": EFFICIENT_MIN_AVG_ROE_PCT,
    }

    if f.avg_roe_pct is None or f.roe_years_checked < EFFICIENT_MIN_YEARS:
        return _dimension(
            "efficient",
            "unknown",
            f"only {f.roe_years_checked} year(s) of ROE stored; "
            f"{EFFICIENT_MIN_YEARS} needed",
            metrics,
        )

    stable = (
        f.roe_stdev_pct is None or f.roe_stdev_pct <= EFFICIENT_MAX_ROE_STDEV_PCT
    )
    passed = f.avg_roe_pct >= EFFICIENT_MIN_AVG_ROE_PCT and stable
    return _dimension(
        "efficient",
        "pass" if passed else "fail",
        f"average ROE {f.avg_roe_pct}% over {f.roe_years_checked} years "
        f"(threshold {EFFICIENT_MIN_AVG_ROE_PCT}%), "
        f"stdev {f.roe_stdev_pct if f.roe_stdev_pct is not None else 'n/a'}",
        metrics,
    )


def _cheap(features: HoldFeatures) -> ChenDimension:
    f = features.fundamentals
    ceiling = pe_ceiling(features.industry)
    metrics = {
        "trailing_pe": f.trailing_pe,
        "cape": f.cape,
        "ceiling": ceiling,
        "latest_eps": f.latest_eps,
        "position_pct": features.price.position_pct,
    }

    if f.trailing_pe is None:
        # Two different absences, one status. Which one it is decides what an
        # operator does about it, so the evidence says.
        reason = (
            "no annual EPS stored"
            if f.latest_eps is None
            else "latest annual EPS is not positive, so PE is undefined"
        )
        return _dimension("cheap", "unknown", f"{reason}; PE not computable", metrics)

    band = "financial" if is_financial(features.industry) else "general"
    cape_ceiling = round(ceiling * CHEAP_CAPE_TOLERANCE, 1)
    metrics["cape_ceiling"] = cape_ceiling

    passed = f.trailing_pe <= ceiling
    evidence = f"trailing PE {f.trailing_pe} against the {band} ceiling of {ceiling}"

    # The cyclical check. A trailing PE is at its most flattering exactly when
    # a cyclical is most dangerous -- earnings peak with the cycle, so the
    # denominator does too. Averaging it over a decade is what catches the
    # steel name printing 8x at the top and calling itself a bargain.
    if f.cape is not None:
        cyclically_ok = f.cape <= cape_ceiling
        passed = passed and cyclically_ok
        evidence += (
            f"; cyclically adjusted PE {f.cape} over {f.cape_years} years "
            f"against {cape_ceiling}"
        )
        if not cyclically_ok:
            evidence += " -- current earnings look cyclically elevated"
    else:
        evidence += "; too few years of EPS for a cyclically adjusted check"

    return _dimension("cheap", "pass" if passed else "fail", evidence, metrics)


def _collect(features: HoldFeatures) -> ChenDimension:
    d = features.dividend
    metrics = {
        "years_with_cash": float(d.years_with_cash),
        "consecutive_years": float(d.consecutive_years_with_cash),
        "cash_yield_pct": d.cash_yield_pct,
        "avg_yield_pct": d.avg_yield_pct,
        "ttm_cash": d.ttm_cash,
        "years_observed": float(d.years_observed),
        "threshold_pct": COLLECT_MIN_YIELD_PCT,
    }

    if d.coverage == "none":
        return _dimension(
            "collect", "unknown", "instrument pays no dividends (index)", metrics
        )
    if d.years_with_cash == 0 and d.coverage == "recent":
        # TPEX publishes the current window only, so an empty record there is
        # an absence of data, not evidence the company pays nothing.
        return _dimension(
            "collect",
            "unknown",
            "TPEX publishes only a recent window; no cash history to judge",
            metrics,
        )
    if d.years_with_cash < COLLECT_MIN_YEARS_OBSERVED and d.coverage != "history":
        return _dimension(
            "collect",
            "unknown",
            f"only {d.years_with_cash} paying year(s) visible under "
            f"'{d.coverage}' coverage",
            metrics,
        )

    yield_pct = d.cash_yield_pct if d.cash_yield_pct is not None else d.avg_yield_pct
    streak = d.consecutive_years_with_cash

    # Either arm alone is a known failure mode. A long streak at a thin yield
    # is a bond substitute the method would not buy; a fat yield on a two-year
    # record is usually a price that has fallen for a reason.
    passed = streak >= COLLECT_STRONG_STREAK_YEARS or (
        streak >= COLLECT_MIN_STREAK_YEARS
        and yield_pct is not None
        and yield_pct >= COLLECT_MIN_YIELD_PCT
    )
    if passed:
        return _dimension(
            "collect",
            "pass",
            f"{streak} consecutive paying year(s), "
            f"{d.years_with_cash} paying year(s) in the last {d.window_years}; "
            f"yield {yield_pct if yield_pct is not None else 'n/a'}% "
            f"(threshold {COLLECT_MIN_YIELD_PCT}%)",
            metrics,
        )

    # It did not clear the bar -- but a streak that runs back to the oldest
    # year we ever fetched is a floor, not a record. `coverage` says TWSE
    # publishes a decade; it does not say we pulled one, and on a fresh
    # database the dividend card has fetched about five years. Failing a
    # company here would be failing it for our gap rather than its own, which
    # is precisely the mistake `unknown` exists to prevent.
    #
    # A shorter streak with a real observed gap behind it is a genuine fail:
    # we watched the years in between and they paid nothing.
    if streak >= d.years_observed:
        return _dimension(
            "collect",
            "unknown",
            f"paid in all {d.years_observed} year(s) of archive pulled so far, "
            f"which is short of the {COLLECT_STRONG_STREAK_YEARS} needed to "
            f"establish continuity -- run dividend_board_warmup",
            metrics,
        )

    return _dimension(
        "collect",
        "fail",
        f"{streak} consecutive paying year(s) within {d.years_observed} year(s) "
        f"of archive, {d.years_with_cash} paying year(s) in the last "
        f"{d.window_years}; yield {yield_pct if yield_pct is not None else 'n/a'}% "
        f"(threshold {COLLECT_MIN_YIELD_PCT}%)",
        metrics,
    )


def _liquid(features: HoldFeatures) -> ChenDimension:
    liq = features.liquidity
    metrics = {
        "avg_daily_shares": (
            float(liq.avg_daily_shares) if liq.avg_daily_shares is not None else None
        ),
        "no_trade_days": float(liq.no_trade_days),
        "trading_days": float(liq.trading_days),
        "threshold_shares": float(LIQUID_MIN_AVG_SHARES),
    }

    if not liq.trading_days or liq.avg_daily_shares is None:
        return _dimension("liquid", "unknown", "no daily bars stored", metrics)

    quiet_ratio = liq.no_trade_days / liq.trading_days
    passed = (
        liq.avg_daily_shares >= LIQUID_MIN_AVG_SHARES
        and quiet_ratio <= LIQUID_MAX_NO_TRADE_RATIO
    )
    return _dimension(
        "liquid",
        "pass" if passed else "fail",
        f"{liq.avg_daily_shares} shares/day over {liq.trading_days} sessions "
        f"(threshold {LIQUID_MIN_AVG_SHARES}), "
        f"{liq.no_trade_days} session(s) with no trade",
        metrics,
    )


def _coverage_gaps(features: HoldFeatures, dimensions: list[ChenDimension]) -> list[str]:
    """Why the checklist could not answer everything, as stable identifiers.

    Keys rather than sentences, so the UI renders them from its own catalogue
    in whichever language the reader is using. `evidence` on each dimension
    carries the detail for a prompt or a log.
    """
    unknown = {d.key for d in dimensions if d.status == "unknown"}
    gaps: list[str] = []

    if features.fundamentals.years_available == 0 and unknown & {
        "earn",
        "efficient",
        "cheap",
    }:
        # One gap, not three: the operator's action is the same for all of them
        # and listing it three times reads as three separate problems.
        gaps.append("no_annual_fundamentals")
    else:
        if "earn" in unknown:
            gaps.append("short_eps_history")
        if "efficient" in unknown:
            gaps.append("short_roe_history")
        if "cheap" in unknown:
            gaps.append("no_trailing_pe")

    if "collect" in unknown:
        if features.dividend.coverage == "recent":
            gaps.append("tpex_recent_dividends_only")
        elif features.dividend.years_observed < COLLECT_STRONG_STREAK_YEARS:
            # Ours to fix, and the message says so: the warmup job pulls the
            # archive this needs.
            gaps.append("dividend_archive_not_warmed")
        else:
            gaps.append("no_dividend_history")
    if "liquid" in unknown:
        gaps.append("no_daily_bars")

    return gaps


def _band(
    score: int, known_weight: int, dimensions: list[ChenDimension]
) -> HoldSuitability:
    """Score to label, with two caps that only ever move the label down.

    A cap never touches `score`. The number says what was passed out of what
    was checked; the word says how much confidence that deserves, and the two
    are allowed to differ as long as the card shows both.
    """
    if score < BAND_STRONG:
        return "ok" if score >= BAND_OK else ("weak" if score >= BAND_WEAK else "avoid")

    thin = known_weight < MIN_KNOWN_WEIGHT_FOR_STRONG
    unchecked_essential = any(
        d.status == "unknown" for d in dimensions if d.key in ESSENTIAL_DIMENSIONS
    )
    return "ok" if thin or unchecked_essential else "strong"


def evaluate(features: HoldFeatures) -> ChenRuleResult:
    """Score the five dimensions. Pure, cheap, and safe to call on every load."""
    dimensions = [
        _earn(features),
        _efficient(features),
        _cheap(features),
        _collect(features),
        _liquid(features),
    ]

    known_weight = sum(d.weight for d in dimensions if d.status != "unknown")
    passed_weight = sum(d.weight for d in dimensions if d.status == "pass")

    # Nothing known means no score, not zero. A zero would render as "this
    # company failed everything" on a card whose real message is "we have not
    # looked at anything yet".
    score = round(passed_weight / known_weight * 100) if known_weight else None

    return ChenRuleResult(
        score=score,
        suitability=(
            _band(score, known_weight, dimensions) if score is not None else None
        ),
        dimensions=dimensions,
        known_weight=known_weight,
        total_weight=TOTAL_WEIGHT,
        coverage_gaps=_coverage_gaps(features, dimensions),
    )


def summarise(result: ChenRuleResult) -> str:
    """One English block for the prompt: the verdict and every line behind it."""
    head = (
        f"score {result.score}/100 -> {result.suitability} "
        f"(judged on {result.known_weight} of {result.total_weight} weight)"
        if result.score is not None
        else "not scored: no dimension had enough data"
    )
    lines = [f"  {d.key}: {d.status} (weight {d.weight}) -- {d.evidence}" for d in result.dimensions]
    if result.coverage_gaps:
        lines.append(f"  coverage gaps: {', '.join(result.coverage_gaps)}")
    return "\n".join([head, *lines])
