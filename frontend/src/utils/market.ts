/** Taiwan regular trading session: Mon–Fri 09:00–13:30, Asia/Taipei.

Read in Taipei time explicitly, because the browser may well be somewhere else
and the exchange schedule is not. Used to decide whether polling the realtime
endpoint is worth doing at all -- outside the session it only burns TWSE
rate-limit budget to return the same last-traded values.
*/
export function isMarketOpen(now: Date = new Date()): boolean {
  const parts = new Intl.DateTimeFormat('en-US', {
    timeZone: 'Asia/Taipei',
    weekday: 'short',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(now)

  const part = (type: string) => parts.find((p) => p.type === type)?.value ?? ''

  const weekday = part('weekday')
  if (weekday === 'Sat' || weekday === 'Sun') return false

  const minutes = Number(part('hour')) * 60 + Number(part('minute'))
  return minutes >= 9 * 60 && minutes <= 13 * 60 + 30
}

/** Today on the exchange's calendar, as 'YYYY-MM-DD'.
 *
 * Same reason `isMarketOpen` reads Taipei time: a browser in Los Angeles is
 * most of a day behind, and asking the open board for *its* today would show
 * yesterday's session -- or, past 08:00 local on the US west coast, a date the
 * exchange has not reached yet. `en-CA` is used only because its short date
 * format is already ISO; the locale is never shown to anyone.
 */
export function taipeiToday(now: Date = new Date()): string {
  return new Intl.DateTimeFormat('en-CA', {
    timeZone: 'Asia/Taipei',
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).format(now)
}
