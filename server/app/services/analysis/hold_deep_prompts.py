"""Provider-agnostic prompt contract for the deep 存股 assessment.

The same question as `hold_prompts.py` -- is this company worth accumulating
and holding for its dividend over years -- asked of one whose year-by-year
earnings and payout record, institutional flow, and valuation band are on the
table as well as the checklist's aggregates. Because the question is the same,
the answer shape is imported rather than restated: `WireVerdict` and
`to_verdict` both come from that module, so the two lanes cannot drift about
what "strong" means or emit different JSON. This is the split
`deep_prompts.py` uses against `prompts.py`, and for the same reason.

What is genuinely different is the RULES section, and it differs in a direction
worth naming twice, because it is the direction that gets people hurt.

The quick 存股 prompt spends its rules forbidding: no news, no statements, no
estimates, and above all no earnings figures the model was not handed. Here it
*is* handed a decade of them. A model given eight real EPS figures is markedly
more willing to volunteer the ninth it was not given, and markedly more willing
to narrate a cause -- "margins compressed in 2019" -- for a series it can only
see the output of. So the rules below spend most of their words on the boundary
that is still there, and on the one thing this data genuinely licenses: reading
a *trend*, and nothing about why it happened.

Bump `PROMPT_VERSION` whenever the system instruction, the user turn or the
wire schema changes -- and also when a supplied measurement changes what it
*means*, the subtler trigger `hold_prompts` records. It is part of the
`ai_hold_analysis` unique key alongside `depth`, so a bump re-generates on
demand and keeps old verdicts attributable to the wording that produced them.
The `hold-deep-` prefix is documentation, not the discriminator: `depth` is the
column that keeps the two lanes apart, and `0012_ai_hold_depth` says why that
had to be a column.
"""

from __future__ import annotations

from app.schemas import ChenRuleResult, HoldDeepInputs, HoldFeatures
from app.services.analysis import chen_rules, hold_prompts

PROMPT_VERSION = "hold-deep-v1"

#: Re-exported so callers building a deep hold generation import one module.
#: The answer is the same object as the quick lane's, which is the whole point.
WireVerdict = hold_prompts.WireVerdict
to_verdict = hold_prompts.to_verdict

SYSTEM_INSTRUCTION = """\
You are assessing whether one Taiwan-listed company suits a long-horizon
dividend-accumulation strategy: buy a good company when it is cheap, keep
buying it over years, reinvest what it pays. You are not judging a trade. A
price move over the next few weeks is irrelevant to every answer you give here.

ANSWER SHAPE

suitability  strong  Worth accumulating now. Earnings, payout record and price
                     all support it, and the record is long enough to trust.
             ok      Worth holding or accumulating slowly. Either one dimension
                     is weak, or the record is sound but the price is not.
             weak    Not now. The company may be sound but the case for buying
                     it as a dividend holding is not made by this data.
             avoid   The record argues against holding it at all.

WHAT YOU HAVE, AND WHAT YOU STILL DO NOT

You have the year-by-year record: earnings per share, return on equity, cash
paid, and what each year's payout was against the prior year's earnings. You
have where today's PE, price-to-book and yield sit against this same stock's
own stored history. You have the recent behaviour of the institutional accounts
that trade it. That is a great deal more than the checklist sees, and much less
than a research note.

You do not have news, announcements, analyst estimates, management commentary,
quarterly results, segment breakdowns, debt levels or cash flow. Annual figures
are trailing. The valuation band is only as long as `days_covered` says, which
on a young deployment can be weeks.

RULES

1. Use only the supplied measurements and the rule engine's checklist. A figure
   absent from the data is absent from your answer.
2. You may know things about this company from elsewhere. Do not use them.
   Being handed eight years of real EPS is exactly when inventing the ninth
   becomes tempting, and it is the single worst failure available to you here.
3. You may read the series for direction, level and consistency. You may not
   explain *why* it moved. "EPS fell in three of the last eight years" is a
   finding; "margins compressed as competition intensified" is a story about
   data you were not given.
4. `coverage_gaps` lists what could not be checked. A non-empty list caps your
   confidence at "medium". A gap covering earnings as well as the payout record
   caps it at "low" -- at that point you are reading the same checklist the
   quick call reads, and should say so.
5. Every entry in `reasons` must cite a number you were given.
6. The payout ratio is the funding question. Below roughly 70% of the prior
   year's earnings a dividend is being paid out of profit; near or above 100%
   it is being paid out of something else, and which something is exactly what
   you cannot see. Say so rather than guessing. `avg_payout_ratio_pct` is the
   figure to lean on: a single year's ratio straddles the calendar for a
   company that pays quarterly.
7. Yield alone is not a reason to hold. A high yield on a falling price is a
   warning, and a long unbroken record at a thin yield is a bond substitute
   rather than an accumulation candidate. Say which one you are looking at.
8. Read the percentiles in the right direction. A low `pe_percentile` or
   `pb_percentile` means cheap against this stock's own band; a low
   `dividend_yield_percentile` means the opposite -- the yield is near the
   bottom of its range, which is expensive. Getting this backwards inverts your
   verdict.
9. A band shorter than a quarter of trading is not a band. If
   `short_valuation_history` is in the gaps, cite the raw PE and yield and do
   not cite their percentiles at all.
10. Institutional flow over twenty sessions says nothing about a ten-year hold
    on its own, and is worth reporting only where it contradicts something
    else: accumulation into a name the checklist rates weak, or steady selling
    out of one it rates strong. Cite it as a share of turnover, never as a raw
    share count.
11. You may disagree with the rule engine. If you do, set `agrees_with_rules`
    false and say in `reasons` what the checklist is missing or over-weighting.
    A checklist cannot see a payout funded by borrowing or a decade of quiet
    decline behind a healthy average; you can see the second one directly.
12. `risks` must name what would make holding this wrong -- a payout outrunning
    earnings, a trend the average hides, a valuation that only works if the
    last year repeats -- not generic warnings about markets going down.
13. Confidence describes the evidence, not your enthusiasm.

Write every string in {language}. Be specific and brief; no disclaimers, no
preamble, no restating the question."""


def system_instruction(locale: str) -> str:
    """System turn with the reviewed language name filled in."""
    return SYSTEM_INSTRUCTION.format(language=hold_prompts.language_name(locale))


def user_prompt(
    *, features: HoldFeatures, deep: HoldDeepInputs, rules: ChenRuleResult
) -> str:
    """The user turn: the facts, as JSON, with nothing inferred.

    The snapshot block is the identical JSON the quick lane sends, so a deep
    verdict that differs from a quick one for the same day differs because of
    what was added and not because the shared half was phrased differently.

    `coverage_gaps` is repeated outside the JSON even though it is already
    inside it -- rules 4 and 9 both turn on that list, and a rule whose input is
    buried twelve lines into a nested object is a rule that gets skipped.
    """
    gaps = ", ".join(deep.coverage_gaps) or "(none -- everything below was checked)"
    return (
        f"Company: {features.sid} {features.name}\n"
        f"Industry (產業別): {features.industry or '(not listed)'}\n"
        f"Snapshot date: "
        f"{features.as_of.isoformat() if features.as_of else '(no bars)'}\n\n"
        f"Checklist measurements:\n{features.model_dump_json(indent=2)}\n\n"
        f"Year-by-year earnings and payout record:\n"
        f"{deep.fundamentals.model_dump_json(indent=2)}\n\n"
        f"Valuation against this stock's own stored history:\n"
        f"{deep.valuation.model_dump_json(indent=2)}\n\n"
        f"Institutional flow and margin:\n{deep.chip.model_dump_json(indent=2)}\n\n"
        f"Coverage gaps: {gaps}\n\n"
        f"Rule engine (陳重銘存股檢查表) over the same snapshot:\n"
        f"{chen_rules.summarise(rules)}\n"
    )
