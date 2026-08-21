import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { Link } from 'react-router-dom'

import { api } from '../api/client'
import { useI18n } from '../i18n'
import { errorMessage } from '../utils/errors'

/**
 * Pick which Gemini model the deployment calls for AI analysis.
 *
 * The closed set comes from GEMINI_MODELS in the environment; this page only
 * chooses among those entries. Changing the live model is ADMIN-only because
 * it is part of every stored verdict's cache key -- a flip starts a new cache
 * lane rather than rewriting yesterday's answers.
 */
export default function AdminAi() {
  const { intlTag, t } = useI18n()
  const queryClient = useQueryClient()
  const settings = useQuery({
    queryKey: ['admin-ai-settings'],
    queryFn: api.getAiSettings,
  })
  const [selected, setSelected] = useState('')

  useEffect(() => {
    if (settings.data) {
      setSelected(settings.data.model)
    }
  }, [settings.data])

  const save = useMutation({
    mutationFn: (model: string) => api.updateAiSettings(model),
    onSuccess: (data) => {
      queryClient.setQueryData(['admin-ai-settings'], data)
    },
  })

  const dirty = Boolean(settings.data && selected && selected !== settings.data.model)

  return (
    <div className="stack">
      <Link to="/admin" className="btn btn-sm" style={{ alignSelf: 'flex-start' }}>
        {t('admin.back')}
      </Link>

      <div className="stack" style={{ gap: 4 }}>
        <h2 className="card-title" style={{ margin: 0 }}>
          {t('adminAi.title')}
        </h2>
        <p className="dim" style={{ margin: 0 }}>
          {t('adminAi.lede')}
        </p>
      </div>

      <section className="card stack" style={{ gap: 14 }}>
        {settings.isLoading && <p className="dim">{t('adminAi.loading')}</p>}
            {settings.isError && (
          <p className="up">{t('adminAi.loadFailed', { message: errorMessage(settings.error, t) })}</p>
        )}

        {settings.data && (
          <>
            <label className="stack" style={{ gap: 6 }}>
              <span className="dim">{t('adminAi.model')}</span>
              <select
                className="input"
                value={selected}
                onChange={(e) => setSelected(e.target.value)}
                disabled={save.isPending}
              >
                {settings.data.available_models.map((id) => (
                  <option key={id} value={id}>
                    {id}
                  </option>
                ))}
              </select>
            </label>

            <p className="dim" style={{ margin: 0, fontSize: '0.9rem' }}>
              {t('adminAi.allowlistHint')}
            </p>

            {(settings.data.updated_by || settings.data.updated_at) && (
              <p className="dim" style={{ margin: 0, fontSize: '0.9rem' }}>
                {t('adminAi.lastUpdated', {
                  who: settings.data.updated_by ?? '—',
                  when: settings.data.updated_at
                    ? new Date(settings.data.updated_at).toLocaleString(intlTag)
                    : '—',
                })}
              </p>
            )}

            {save.isError && (
              <p className="up">{t('adminAi.saveFailed', { message: errorMessage(save.error, t) })}</p>
            )}
            {save.isSuccess && !dirty && (
              <p className="dim" style={{ margin: 0 }}>
                {t('adminAi.saved')}
              </p>
            )}

            <div className="row" style={{ gap: 8 }}>
              <button
                type="button"
                className="btn"
                disabled={!dirty || save.isPending || !selected}
                onClick={() => save.mutate(selected)}
              >
                {save.isPending ? t('adminAi.saving') : t('adminAi.save')}
              </button>
            </div>
          </>
        )}
      </section>
    </div>
  )
}
