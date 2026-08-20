import { useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '../api/client'
import type { Role, User } from '../api/types'
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

  const error = users.error ?? update.error ?? remove.error

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
                <th>註冊時間</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {(users.data?.users ?? []).map((row: User) => {
                // The server refuses these too; disabling them here just avoids
                // offering an action that cannot succeed.
                const isSelf = row.id === me?.id
                const busy = update.isPending || remove.isPending

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
                    <td className="dim tabular">
                      {new Date(row.created_at).toLocaleDateString('zh-TW')}
                    </td>
                    <td>
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
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>

          <p className="dim" style={{ marginTop: 12 }}>
            共 {users.data?.total ?? 0} 個帳號。系統至少要保留一位啟用中的
            ADMIN，也無法變更自己的角色或狀態。
          </p>
        </section>
      )}
    </div>
  )
}
