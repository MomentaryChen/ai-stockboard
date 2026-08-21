/**
 * One catalogue of coverage-gap slugs, shared by every panel that reports them.
 *
 * The server answers "what could not be checked" as stable snake_case
 * identifiers rather than sentences, so the wording lives on this side and
 * follows the reader's language. Two panels now render the same slugs -- the
 * 存股 checklist and the deep AI verdict both say "no annual fundamentals" when
 * `fundamentals_annual` is empty -- and two local copies of this map would mean
 * the same condition explained two different ways depending on which card you
 * happened to be looking at.
 *
 * An unrecognised slug is rendered as itself by the callers rather than as
 * blank: a new gap shipped by the server should look untranslated, not absent.
 */

import type { MessageKey } from '.'

export const GAP_KEY: Record<string, MessageKey> = {
  // Annual figures. Shared by both lanes -- until the fundamentals ingest
  // lands, `no_annual_fundamentals` is the normal answer for every stock.
  no_annual_fundamentals: 'gap.noFundamentals',
  short_eps_history: 'gap.shortEps',
  short_roe_history: 'gap.shortRoe',
  no_trailing_pe: 'gap.noPe',

  // Dividends. 存股 lane only; the technical lanes do not read them.
  tpex_recent_dividends_only: 'gap.tpexRecent',
  dividend_archive_not_warmed: 'gap.archiveNotWarmed',
  no_dividend_history: 'gap.noDividends',
  no_daily_bars: 'gap.noBars',

  // Institutional flow and margin. Deep AI lane only.
  no_chip_data: 'gap.noChipData',
  partial_chip_coverage: 'gap.partialChipCoverage',
  no_margin_data: 'gap.noMarginData',
}
