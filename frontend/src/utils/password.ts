/**
 * The client-side half of the server's password rules.
 *
 * `server/app/security.py` is the authority -- this only exists so a rejection
 * shows up as you type instead of after a round trip, and so both the register
 * form and the change-password form say the same thing. Keep the two numbers in
 * step with MIN_PASSWORD_LENGTH / MAX_PASSWORD_BYTES over there.
 */

import type { MessageKey, TParams } from '../i18n/types'

export const MIN_PASSWORD_LENGTH = 8

// bcrypt hashes at most the first 72 bytes, and the server rejects anything
// longer rather than truncating it silently. A Chinese character is 3 bytes,
// so this is reachable with 25 of them -- check it here for a faster answer.
export const MAX_PASSWORD_BYTES = 72

/**
 * A problem is returned as a message key, not a sentence: this module is not a
 * component, so it has no `t` to call, and holding the key lets the form
 * re-render the complaint in the new language when the locale changes.
 */
export interface PasswordProblem {
  key: MessageKey
  params?: TParams
}

/** null when the password is usable, otherwise why it is not. */
export function passwordProblem(
  password: string,
  confirm: string,
): PasswordProblem | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return { key: 'password.tooShort', params: { min: MIN_PASSWORD_LENGTH } }
  }
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
    return { key: 'password.tooLong', params: { max: MAX_PASSWORD_BYTES } }
  }
  if (password !== confirm) return { key: 'password.mismatch' }
  return null
}
