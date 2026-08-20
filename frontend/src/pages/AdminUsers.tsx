import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { PasswordResetResponse, Role, User } from '../api/types'
import { useAuth } from '../auth/AuthContext'

export default function AdminUsers() {
  const { user: me } = useAuth()
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
          使用者管理
        </h2>
        <input
          className="text-input"
          style={{ maxWidth: 260 }}
          placeholder="搜尋帳號或 Email"
          value={q}
          onChange={(event) => setQ(event.target.value)}
        />
      </div>

      {error && (
        <div className="banner banner-error">操作失敗：{(error as Error).message}</div>
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
                <th>帳號</th>
                <th>Email</th>
                <th>手機</th>
                <th>角色</th>
                <th>狀態</th>
                <th>密碼</th>
                <th>註冊時間</th>
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
                        title={isSelf ? '不能變更自己的角色' : undefined}
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
                        {row.is_active ? '啟用中' : '已停用'}
                      </button>
                    </td>
                    <td className="dim">
                      {row.must_change_password ? '待重設' : '—'}
                    </td>
                    <td className="dim tabular">
                      {new Date(row.created_at).toLocaleDateString('zh-TW')}
                    </td>
                    <td>
                      <div className="row" style={{ gap: 6 }}>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={isSelf || busy}
                          title={
                            isSelf
                              ? '要改自己的密碼請用「變更密碼」'
                              : undefined
                          }
                          onClick={() => {
                            if (
                              window.confirm(
                                `確定要重設 ${row.username} 的密碼？

` +
                                  '系統會產生一組臨時密碼並「只顯示一次」，' +
                                  '該帳號現有的登入狀態會全部失效，' +
                                  '而且必須先設定新密碼才能使用其他功能。',
                              )
                            ) {
                              resetPassword.mutate(row.id)
                            }
                          }}
                        >
                          重設密碼
                        </button>
                        <button
                          type="button"
                          className="btn btn-sm"
                          disabled={isSelf || busy}
                          onClick={() => {
                            if (
                              window.confirm(
                                `確定要刪除 ${row.username}？此帳號的自選股也會一併移除。`,
                              )
                            ) {
                              remove.mutate(row.id)
                            }
                          }}
                        >
                          刪除
                        </button>
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <p className="dim" style={{ marginTop: 12 }}>
            共 {users.data?.total ?? 0} 個帳號。系統至少要保留一位啟用中的
            ADMIN，也無法變更自己的角色或狀態。「待重設」表示該帳號拿的是臨時密碼，
            登入後只能設定新密碼。
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
  const [copied, setCopied] = useState(false)

  return (
    <div className="banner banner-warn">
      <div className="stack" style={{ gap: 10 }}>
        <div>
          已為 <strong>{result.user.username}</strong>（{result.user.email}）
          產生臨時密碼。<strong>這組密碼只會顯示這一次</strong>
          ，關閉後就無法再查看，請立即透過可信任的管道交給對方。
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
            {copied ? '已複製' : '複製'}
          </button>
          <button type="button" className="btn btn-sm" onClick={onDismiss}>
            我已收好，關閉
          </button>
        </div>

        <div className="dim">
          對方登入後必須先設定自己的新密碼，在那之前其他功能都不能用；
          該帳號原有的登入狀態也已全部失效。
        </div>
      </div>
    </div>
  )
}
