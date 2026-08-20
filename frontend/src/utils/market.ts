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
