import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import type { PasswordResetResponse, Role, User } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'
import { errorMessage } from '../utils/errors'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const { intlTag, t } = useI18n()
  const queryClient = useQueryClient()
  const [q, setQ] = useState('')
  const [pendingOnly, setPendingOnly] = useState(false)

  const users = useQuery({
    queryKey: ['users', q, pendingOnly],
    queryFn: () => api.listUsers(q, pendingOnly),
  })

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ['users'] })

  const update = useMutation({
    mutationFn: ({
      id,
      body,
    }: {
      id: number
      body: { role?: Role; is_active?: boolean }
    }) => api.updateUser(id, body),
    onSuccess: invalidate,
  })

  const remove = useMutation({
    mutationFn: (id: number) => api.deleteUser(id),
    onSuccess: invalidate,
  })

  const unlock = useMutation({
    mutationFn: (id: number) => api.unlockUser(id),
    onSuccess: invalidate,
  })

  // The generated password, held only in this component's state and only until
  // the admin dismisses it. Never written to storage or a query cache: the
  // server keeps no copy, so anything that outlives the page is a leak with no
  // upside.
  const [issued, setIssued] = useState<PasswordResetResponse | null>(null)

  const resetPassword = useMutation({
    mutationFn: (id: number) => api.resetUserPassword(id),
    onSuccess: (data) => {
      setIssued(data)
      invalidate()
    },
  })

  const error =
    users.error ??
    update.error ??
    remove.error ??
    unlock.error ??
    resetPassword.error

  const pendingTotal = users.data?.pending_total ?? 0

  return (
    <div className="stack">
      <Link to="/admin" className="back-link">
        {t('admin.back')}
      </Link>
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('adminUsers.title')}
        </h2>
        <div className="row wrap" style={{ gap: 10 }}>
          <label className="row dim" style={{ gap: 6 }}>
            <input
              type="checkbox"
              checked={pendingOnly}
              onChange={(event) => setPendingOnly(event.target.checked)}
            />
            {t('adminUsers.pendingOnly')}
          </label>
          <input
            className="text-input"
            style={{ maxWidth: 260 }}
            placeholder={t('adminUsers.searchPlaceholder')}
            value={q}
            onChange={(event) => setQ(event.target.value)}
          />
        </div>
      </div>

      {/* The queue is the one thing on this page with a deadline attached --
          somebody is waiting on the other end of it -- so it is stated above
          the table rather than left to be noticed in a row. */}
      {pendingTotal > 0 && (
        <div className="banner banner-warn">
          {t('adminUsers.pendingBanner', { count: pendingTotal })}
        </div>
      )}

      {error && (
        <div className="banner banner-error">
          {t('adminUsers.opFailed', { message: errorMessage(error, t) })}
        </div>
      )}

      {issued && (
        <TempPasswordNotice
          result={issued}
          onDismiss={() => setIssued(null)}
        />
      )}

      {users.isPending ? (
        <div className="center-note">
          <span className="spinner" />
        </div>
      ) : (
        <section className="card">
          <table className="data">
            <thead>
              <tr>
                <th>{t('adminUsers.colUsername')}</th>
                <th>{t('adminUsers.colEmail')}</th>
                <th>{t('adminUsers.colPhone')}</th>
                <th>{t('adminUsers.colRole')}</th>
                <th>{t('adminUsers.colStatus')}</th>
                <th>{t('adminUsers.colPassword')}</th>
                <th>{t('adminUsers.colCreatedAt')}</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(users.data?.users ?? []).map((row: User) => {
                // The server refuses these too; disabling them here just avoids
                // offering an action that cannot succeed.
                const isSelf = row.id === me?.id
                const busy =
                  update.isPending ||
                  remove.isPending ||
                  unlock.isPending ||
                  resetPassword.isPending
                // Compared against now rather than trusted as a flag: the
                // timestamp is in the past for most of its life, and a row
                // cached from before it expired would otherwise still claim
                // the account is locked.
                const locked =
                  row.locked_until !== null && new Date(row.locked_until) > new Date()

                return (
                  <tr key={row.id}>
                    <td>{row.username}</td>
                    <td>{row.email}</td>
                    <td className="dim">{row.phone ?? '—'}</td>
                    <td>
                      <select
                        className="text-input"
                        style={{ maxWidth: 110 }}
                        value={row.role}
                        disabled={isSelf || busy}
                        title={isSelf ? t('adminUsers.selfRoleTitle') : undefined}
                        onChange={(event) =>
                          update.mutate({
                            id: row.id,
                            body: { role: event.target.value as Role },
                          })
                        }
                      >
                        <option value="USER">USER</option>
                        <option value="ADMIN">ADMIN</option>
                      </select>
                    </td>
                    <td>
                      {/* Approving and activating are the same switch on the
                          server, so they are one button here too -- only the
                          wording changes, because "啟用" does not tell an
                          admin that somebody is waiting on the answer. */}
                      <button
                        type="button"
                        className={`btn btn-sm ${
                          row.pending_approval
                            ? 'btn-primary'
                            : row.is_active
                              ? 'active'
                              : ''
                        }`}
                        disabled={isSelf || busy}
                        onClick={() =>
                          update.mutate({
                            id: row.id,
                            body: { is_active: !row.is_active },
                          })
                        }
                      >
                        {row.pending_approval
                          ? t('adminUsers.approve')
                          : row.is_active
                            ? t('adminUsers.statusActive')
                            : t('adminUsers.statusInactive')}
                      </button>
                    </td>
                    <td className="dim">
                      {locked
                        ? t('adminUsers.lockedUntil', {
                            when: new Date(row.locked_until as string).toLocaleTimeString(
                              intlTag,
                              { hour: '2-digit', minute: '2-digit' },
                            ),
                          })
                        : row.must_change_password
                          ? t('adminUsers.pendingReset')
                          : '—'}
                    </td>
                    <td className="dim tabular">
                      {new Date(row.created_at).toLocaleDateString(intlTag)}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        {/* Offered only while it would do something. Unlike
                            the others this one is allowed on your own row: an
                            admin locked out in one browser can still be signed
                            in in another, and that is exactly when they need
                            it. */}
                        {locked && (
                          <button
                            type="button"
                            className="btn btn-sm"
                            disabled={busy}
                            onClick={() => unlock.mutate(row.id)}
                          >
                            {t('adminUsers.unlock')}
                          </button>
                        )}
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={isSelf || busy}
                          title={
                            isSelf ? t('adminUsers.selfPasswordTitle') : undefined
                          }
                          onClick={() => {
                            if (
                              window.confirm(
                                t('adminUsers.resetConfirm', {
                                  username: row.username,
                                }),
                              )
                            ) {
                              resetPassword.mutate(row.id)
                            }
                          }}
                        >
                          {t('adminUsers.resetPassword')}
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={isSelf || busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                t('adminUsers.deleteConfirm', {
                                  username: row.username,
                                }),
                              )
                            ) {
                              remove.mutate(row.id)
                            }
                          }}
                        >
                          {t('adminUsers.delete')}
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <p className="dim" style={{ marginTop: 12 }}>
            {t('adminUsers.footer', { total: users.data?.total ?? 0 })}
          </p>
        </section>
      )}
    </div>
  )
}

/**
 * Shows a freshly generated password, once.
 *
 * The server stores only the bcrypt hash, so this render is the only place the
 * plaintext will ever exist -- dismissing it destroys it. Said plainly on the
 * panel, because an admin who assumes they can look it up again will close it
 * and lock the user out until the next reset.
 */
function TempPasswordNotice({
  result,
  onDismiss,
}: {
  result: PasswordResetResponse
  onDismiss: () => void
}) {
  const { t } = useI18n()
  const [copied, setCopied] = useState(false)

  return (
    <div className="banner banner-warn">
      <div className="stack" style={{ gap: 10 }}>
        <div>
          {t('adminUsers.tempIssuedFor', {
            username: result.user.username,
            email: result.user.email,
          })}{' '}
          <strong>{t('adminUsers.tempIssuedOnce')}</strong>
        </div>

        <div className="row wrap" style={{ gap: 8 }}>
          <code className="temp-password">{result.temp_password}</code>
          <button
            type="button"
            className="btn btn-sm"
            onClick={async () => {
              try {
                await navigator.clipboard.writeText(result.temp_password)
                setCopied(true)
              } catch {
                // Clipboard access needs a secure context, which plain-HTTP
                // deployments do not have. The password is on screen either
                // way, so this is a convenience that is allowed to fail.
                setCopied(false)
              }
            }}
          >
            {copied ? t('adminUsers.copied') : t('adminUsers.copy')}
          </button>
          <button type="button" className="btn btn-sm" onClick={onDismiss}>
            {t('adminUsers.dismiss')}
          </button>
        </div>

        <div className="dim">{t('adminUsers.tempIssuedNote')}</div>
      </div>
    </div>
  )
}
