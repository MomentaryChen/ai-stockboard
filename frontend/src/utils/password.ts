/**
 * The client-side half of the server's password rules.
 *
 * `server/app/security.py` is the authority -- this only exists so a rejection
 * shows up as you type instead of after a round trip, and so both the register
 * form and the change-password form say the same thing. Keep the two numbers in
 * step with MIN_PASSWORD_LENGTH / MAX_PASSWORD_BYTES over there.
 */

export const MIN_PASSWORD_LENGTH = 8

// bcrypt hashes at most the first 72 bytes, and the server rejects anything
// longer rather than truncating it silently. A Chinese character is 3 bytes,
// so this is reachable with 25 of them -- check it here for a faster answer.
export const MAX_PASSWORD_BYTES = 72

/** null when the password is usable, otherwise why it is not. */
export function passwordProblem(password: string, confirm: string): string | null {
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `密碼至少要 ${MIN_PASSWORD_LENGTH} 個字元`
  }
  if (new TextEncoder().encode(password).length > MAX_PASSWORD_BYTES) {
    return `密碼不可超過 ${MAX_PASSWORD_BYTES} 位元組（中文約 24 字）`
  }
  if (password !== confirm) return '兩次輸入的密碼不一致'
  return null
}
