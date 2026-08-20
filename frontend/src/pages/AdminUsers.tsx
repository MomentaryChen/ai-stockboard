import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { PasswordResetResponse, Role, User } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import { useI18n } from '../i18n'

export default function AdminUsers() {
  const { user: me } = useAuth()
  const { intlTag, t } = useI18n()
  const queryClient = useQueryClient()
  const [q, setQ] = useState('')

  const users = useQuery({
    queryKey: ['users', q],
    queryFn: () => api.listUsers(q),
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
    users.error ?? update.error ?? remove.error ?? resetPassword.error

  return (
    <div className="stack">
      <div className="row-between wrap" style={{ gap: 16 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('adminUsers.title')}
        </h2>
        <input
          className="text-input"
          style={{ maxWidth: 260 }}
          placeholder={t('adminUsers.searchPlaceholder')}
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>

      {error && (
        <div className="banner banner-error">
          {t('adminUsers.opFailed', { message: (error as Error).message })}
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
                  update.isPending || remove.isPending || resetPassword.isPending

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
                      <button
                        type="button"
                        className={`btn btn-sm ${row.is_active ? 'active' : ''}`}
                        disabled={isSelf || busy}
                        onClick={() =>
                          update.mutate({
                            id: row.id,
                            body: { is_active: !row.is_active },
                          })
                        }
                      >
                        {row.is_active
                          ? t('adminUsers.statusActive')
                          : t('adminUsers.statusInactive')}
                      </button>
                    </td>
                    <td className="dim">
                      {row.must_change_password ? t('adminUsers.pendingReset') : '—'}
                    </td>
                    <td className="dim tabular">
                      {new Date(row.created_at).toLocaleDateString(intlTag)}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
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
