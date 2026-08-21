"""Provider-agnostic prompt contract for the deep position call.

The same question as `prompts.py` -- enter, exit or hold, and at what size --
asked of a stock whose institutional flow and annual figures are on the table
as well as its price series. Because the question is the same, the answer shape
is imported rather than restated: `ANSWER_SHAPE`, `WireVerdict` and
`to_verdict` all come from that module, so the two lanes cannot drift about
what "large" means or emit different JSON.

What is genuinely different is the RULES section, and it is different in a
direction worth naming. The quick prompt spends its rules forbidding things:
no news, no earnings, no institutional flow, do not reason from memory. Half of
that prohibition is lifted here, which makes the remaining half more dangerous
rather than less -- a model handed real EPS figures is markedly more willing to
volunteer the ones it was not handed. So the rules below spend most of their
words on the boundary that is still there.

Bump `PROMPT_VERSION` whenever the system instruction, the user turn or the
wire schema changes. It is part of the `ai_analysis` unique key alongside
`depth`, so a bump re-generates on demand and keeps old verdicts attributable
to the wording that produced them. The `deep-` prefix is documentation, not
the discriminator: `depth` is the column that keeps the two lanes apart, and
`0010_ai_depth` says why that had to be a column.
"""

from __future__ import annotations

from app.schemas import BestFourPointResult, DeepInputs, PriceFeatures
from app.services.analysis import prompts

PROMPT_VERSION = "deep-v1"

#: Re-exported so callers building a deep generation import one module. The
#: answer is the same object as the quick lane's, which is the whole point.
WireVerdict = prompts.WireVerdict
to_verdict = prompts.to_verdict

SYSTEM_INSTRUCTION = """\
You are a disciplined analyst reading one Taiwan-listed instrument.

You will be given derived measurements of its price series, the recent
behaviour of the institutional accounts that trade it, whatever annual earnings
figures are on record, and the verdict a deterministic rule engine reached from
the bars alone. Decide what a holder should do with their position.

{answer_shape}

WHAT YOU HAVE, AND WHAT YOU STILL DO NOT

You have price and volume, institutional net buying and selling, margin and
short balances, and annual EPS and ROE where they exist. That is more than a
chart and much less than a research note.

You do not have news, announcements, analyst estimates, management commentary,
quarterly results, or anything that happened today. You do not know why the
institutions traded. Annual figures are trailing and can be more than a year
old.

RULES

1. Use only the supplied measurements. `coverage_gaps` lists what could not be
   checked; a figure absent from the data is absent from your answer.
2. You may know things about this company from elsewhere. Do not use them.
   Stating an earnings figure you were not given is the single worst failure
   available to you here, and having been given *some* fundamentals is exactly
   when it becomes tempting.
3. A non-empty `coverage_gaps` caps your confidence at "medium". A gap covering
   institutional flow as well as fundamentals caps it at "low" -- at that point
   you are reading the same chart the quick call reads, and should say so.
4. Every entry in `reasons` must cite a number you were given.
5. Cite institutional flow as a share of turnover, not as a raw share count.
   "Foreign accounts bought 3.1% of five-day turnover across six straight
   sessions" is a reason; "foreign investors bought 8 000 000 shares" is a
   number without a scale. A long streak of nets that round to nothing near
   zero percent is not accumulation, and saying so is a valid finding.
6. Where price and flow disagree, that disagreement is the most useful thing
   you can report -- a rally the institutions sold into, or a drawdown they
   bought. Name it explicitly when you see it.
7. Annual earnings tell you whether a move has anything behind it. They do not
   tell you what happens next quarter, and a low trailing PE on a falling price
   is as often a warning as an opportunity.
8. Rising margin balance into a rally is crowd leverage, not confirmation.
   Treat it as a risk rather than as agreement.
9. "hold" is a correct and expected answer. Mixed evidence is a reason to hold,
   and so is a move that has already happened. Do not manufacture a trade.
10. `risks` must name what would make this call wrong, not generic warnings
    about market conditions. If a coverage gap is what could make it wrong, say
    which one.
11. You may agree or disagree with the rule engine, which saw only the bars. If
    you disagree, say so in `reasons` and say what it could not see.
12. Confidence describes the evidence, not your enthusiasm.

Write every string in {language}. Be specific and brief; no disclaimers, no
preamble, no restating the question."""


def system_instruction(locale: str) -> str:
    """System turn with the shared answer shape and reviewed language filled in."""
    return SYSTEM_INSTRUCTION.format(
        answer_shape=prompts.ANSWER_SHAPE,
        language=prompts.language_name(locale),
    )


def user_prompt(
    *,
    sid: str,
    name: str,
    features: PriceFeatures,
    deep: DeepInputs,
    traditional: BestFourPointResult,
) -> str:
    """The user turn: the facts, as JSON, with nothing inferred.

    The price block is the identical JSON the quick lane sends, so a deep
    verdict that differs from a quick one for the same day differs because of
    what was added and not because the shared half was phrased differently.

    `coverage_gaps` is repeated outside the JSON even though it is already
    inside it. Rule 3 turns on that list, and a rule whose input is buried
    twelve lines into a nested object is a rule that gets skipped.
    """
    gaps = ", ".join(deep.coverage_gaps) or "(none -- everything below was checked)"
    return (
        f"Instrument: {sid} {name}\n"
        f"Trading day: {features.as_of.isoformat()}\n\n"
        f"Price and volume measurements:\n{features.model_dump_json(indent=2)}\n\n"
        f"Institutional flow and margin:\n{deep.chip.model_dump_json(indent=2)}\n\n"
        f"Annual fundamentals:\n{deep.fundamentals.model_dump_json(indent=2)}\n\n"
        f"Coverage gaps: {gaps}\n\n"
        f"Rule engine (四大買賣點) verdict, from the bars alone:\n"
        f"  signal: {traditional.signal}\n"
        f"  label: {traditional.label}\n"
        f"  reasons: {'; '.join(traditional.reasons) or '(none given)'}\n"
    )
