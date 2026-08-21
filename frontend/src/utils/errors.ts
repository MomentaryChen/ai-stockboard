/** Turning a thrown value into the sentence a user reads.
 *
 *  Server `detail` strings are English on purpose (CLAUDE.md: error strings are
 *  engineering communication) and are shown verbatim, because paraphrasing them
 *  drops the detail that makes them useful. The exception is a failure the
 *  server never saw: a request this client gave up on has no `detail` to show,
 *  so the sentence is ours to write -- and product copy lives in the catalogue.
 *
 *  Takes `t` rather than reading the catalogue itself; see the note in jobs.ts.
 */

import { isTimeout } from '../api/client'
import type { Translate } from '../i18n'

export function errorMessage(error: unknown, t: Translate): string {
  if (isTimeout(error)) return t('error.timeout')
  return (error as Error).message
}
