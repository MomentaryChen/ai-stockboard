/**
 * Vocabulary the API sends back as Chinese text.
 *
 * These are product copy the server owns deliberately (CLAUDE.md keeps UI copy
 * Chinese, and `services/jobs/registry.py` says so explicitly for job labels),
 * and every one of them is a closed set: the Best Four Point reasons come out
 * of twstock's `BEST_BUY_WHY` / `BEST_SELL_WHY` plus the hold reasons in
 * `analysis/traditional.py`, market and type come from the exchange ISIN
 * listing, and job metadata comes from the registry. So the honest fix is a
 * lookup here rather than a translation endpoint.
 *
 * Two rules hold throughout:
 *
 *   * Anything not in the set falls through unchanged, so a string added
 *     upstream tomorrow shows the server's own words instead of a blank.
 *   * Job names and stat columns are keyed on the job id and the stats key --
 *     both English identifiers -- rather than on the Chinese label, so a
 *     reworded label upstream does not silently drop the translation.
 *
 * Company names are *not* translated: 台積電 is the name of the instrument, and
 * an English reader searching for it needs the string the exchange publishes.
 */

import type { MessageKey, Translate } from './types'

/** twstock's reason strings plus the hold reasons the server composes. */
const REASON_KEYS: Record<string, MessageKey> = {
  量大收紅: 'bfpReason.buyHeavyVolumeUp',
  量縮價不跌: 'bfpReason.buyVolumeDownPriceHeld',
  三日均價由下往上: 'bfpReason.buyMa3TurnedUp',
  三日均價大於六日均價: 'bfpReason.buyMa3AboveMa6',
  量大收黑: 'bfpReason.sellHeavyVolumeDown',
  量縮價跌: 'bfpReason.sellVolumeDownPriceFell',
  三日均價由上往下: 'bfpReason.sellMa3TurnedDown',
  三日均價小於六日均價: 'bfpReason.sellMa3BelowMa6',
  '買點未成立：近 5 日並非持續超賣（3 日均未全部低於 6 日均）': 'bfpReason.buyNotOversold',
  '買點未成立：乖離仍在探底（谷底在今天，轉折尚未確認）': 'bfpReason.buyStillBottoming',
  '買點未成立：超賣谷底不在昨日或前日，轉折窗口已過': 'bfpReason.buyWindowPassed',
  '賣點未成立：近 5 日沒有超買（3 日均未高於 6 日均）': 'bfpReason.sellNotOverbought',
  '賣點未成立：乖離仍在走高（高點在今天，轉折尚未確認）': 'bfpReason.sellStillRising',
  '賣點未成立：超買高點不在昨日或前日，轉折窗口已過': 'bfpReason.sellWindowPassed',
  四大買點條件皆不符合: 'bfpReason.noBuyConditions',
  四大賣點條件皆不符合: 'bfpReason.noSellConditions',
  '已通過買點乖離關卡，但四大買點條件皆不符合': 'bfpReason.biasPassedNoBuy',
  '已通過賣點乖離關卡，但四大賣點條件皆不符合': 'bfpReason.biasPassedNoSell',
  '尚未載入日線，點進個股頁即可補齊': 'bfpReason.noDailyBars',
}

/** The one reason carrying a number, so it cannot be a plain table lookup. */
const NEED_SAMPLES = /^需要至少\s*(\d+)\s*個交易日才能判斷$/

export function translateBfpReason(reason: string, t: Translate): string {
  const key = REASON_KEYS[reason]
  if (key) return t(key)

  const samples = NEED_SAMPLES.exec(reason)
  if (samples) return t('bfpReason.needSamples', { count: samples[1] })

  return reason
}

/** 'Buy' / 'Sell' / "Don't touch" already arrive in English; only this one is not. */
export function translateBfpLabel(label: string, t: Translate): string {
  return label === '資料不足' ? t('bfpLabel.insufficient') : label
}

const MARKET_KEYS: Record<string, MessageKey> = {
  上市: 'stockMarket.twse',
  上櫃: 'stockMarket.tpex',
}

export function translateMarket(market: string, t: Translate): string {
  const key = MARKET_KEYS[market]
  return key ? t(key) : market
}

const TYPE_KEYS: Record<string, MessageKey> = {
  股票: 'stockType.stock',
  ETF: 'stockType.etf',
  ETN: 'stockType.etn',
  '上市認購(售)權證': 'stockType.warrantTwse',
  '上櫃認購(售)權證': 'stockType.warrantTpex',
  受益證券: 'stockType.beneficiary',
}

export function translateStockType(type: string, t: Translate): string {
  const key = TYPE_KEYS[type]
  return key ? t(key) : type
}

/** Dividend event kinds: single characters on the wire (息 / 權 / 權息). */
const DIVIDEND_KIND_KEYS: Record<string, MessageKey> = {
  息: 'dividendKind.cash',
  權: 'dividendKind.stock',
  權息: 'dividendKind.both',
}

export function translateDividendKind(kind: string, t: Translate): string {
  const key = DIVIDEND_KIND_KEYS[kind]
  return key ? t(key) : kind
}

/**
 * Job registry metadata.
 *
 * Keyed on the job id, which is the same English identifier the API and the
 * routes use, so adding a job to the registry without touching the catalogue
 * still renders -- it just renders the server's Chinese name, which is exactly
 * what the generic console was designed to do.
 */
const JOB_KEYS: Record<string, { name: MessageKey; description: MessageKey }> = {
  stock_code_sync: { name: 'jobName.stock_code_sync', description: 'jobDesc.stock_code_sync' },
  refresh_token_cleanup: {
    name: 'jobName.refresh_token_cleanup',
    description: 'jobDesc.refresh_token_cleanup',
  },
  chip_refresh: { name: 'jobName.chip_refresh', description: 'jobDesc.chip_refresh' },
}

export function translateJobName(id: string, fallback: string, t: Translate): string {
  const keys = JOB_KEYS[id]
  return keys ? t(keys.name) : fallback
}

export function translateJobDescription(
  id: string,
  fallback: string,
  t: Translate,
): string {
  const keys = JOB_KEYS[id]
  return keys ? t(keys.description) : fallback
}

/** Column headers for `job_run.stats`, keyed on the stats key rather than the
 *  server's header text -- see the note at the top of this file. */
const JOB_STAT_KEYS: Record<string, MessageKey> = {
  inserted: 'jobStat.inserted',
  updated: 'jobStat.updated',
  delisted: 'jobStat.delisted',
  pruned: 'jobStat.pruned',
  active: 'jobStat.active',
  deleted: 'jobStat.deleted',
  fetched: 'jobStat.fetched',
  cached: 'jobStat.cached',
  rows: 'jobStat.rows',
}

export function translateJobStat(key: string, fallback: string, t: Translate): string {
  const messageKey = JOB_STAT_KEYS[key]
  return messageKey ? t(messageKey) : fallback
}
