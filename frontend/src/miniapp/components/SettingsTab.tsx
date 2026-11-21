import { useEffect, useState } from 'react'
import { Check, Save } from 'lucide-react'
import type { TelegramDeliveryMode, TelegramScenario, TelegramSettings } from '../../types'
import { errorText } from '../api'
import { Alert, Spinner } from './ui'

const DELIVERY_OPTIONS: Array<{ value: TelegramDeliveryMode; label: string; hint: string }> = [
  { value: 'alerts', label: 'Кратко', hint: 'Короткий итог проверки' },
  { value: 'jsonc', label: 'JSON', hint: 'Только файл отчёта' },
  { value: 'both', label: 'Оба', hint: 'Итог и файл отчёта' },
]

function knownScenario(scenarios: TelegramScenario[], value: string | null): string {
  return value && scenarios.some((item) => item.id === value) ? value : ''
}

interface SettingsTabProps {
  displayName: string
  settings: TelegramSettings
  scenarios: TelegramScenario[]
  onSave: (patch: { deliveryMode: TelegramDeliveryMode; scenario: string | null }) => Promise<TelegramSettings>
}

export function SettingsTab({ displayName, settings, scenarios, onSave }: SettingsTabProps) {
  const [deliveryMode, setDeliveryMode] = useState<TelegramDeliveryMode>(settings.deliveryMode)
  const [scenario, setScenario] = useState<string>(knownScenario(scenarios, settings.scenario))
  const [saved, setSaved] = useState<TelegramSettings>({
    ...settings,
    scenario: knownScenario(scenarios, settings.scenario) || null,
  })
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedNotice, setSavedNotice] = useState(false)

  useEffect(() => {
    setSaved({ ...settings, scenario: knownScenario(scenarios, settings.scenario) || null })
    setDeliveryMode(settings.deliveryMode)
    setScenario(knownScenario(scenarios, settings.scenario))
  }, [settings, scenarios])

  const dirty = deliveryMode !== saved.deliveryMode || (scenario || null) !== saved.scenario
  const scenarioName = scenarios.find((item) => item.id === scenario)?.name

  function edit(next: () => void) {
    next()
    setSavedNotice(false)
    setError(null)
  }

  async function submit(event: React.FormEvent) {
    event.preventDefault()
    if (saving || !dirty) return
    setSaving(true)
    setError(null)
    setSavedNotice(false)
    try {
      const next = await onSave({ deliveryMode, scenario: scenario || null })
      setSaved(next)
      setDeliveryMode(next.deliveryMode)
      setScenario(next.scenario || '')
      setSavedNotice(true)
    } catch (failure) {
      setError(errorText(failure))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="mini-stack">
      <div className="mini-screen-head">
        <div>
          <h1 className="mini-screen-title">Настройки</h1>
          <p className="mini-screen-sub">Как присылать результаты и что запускать.</p>
        </div>
      </div>

      <form className="mini-stack" onSubmit={submit}>
        <section className="mini-card" aria-labelledby="mini-delivery-title">
          <h2 id="mini-delivery-title" className="mini-card__title">Как присылать результат</h2>
          <p className="mini-card__hint">Выберите один вариант — он сохранится для этого аккаунта.</p>

          <div className="mini-seg" role="group" aria-labelledby="mini-delivery-title">
            {DELIVERY_OPTIONS.map((option) => (
              <button
                type="button"
                key={option.value}
                aria-pressed={deliveryMode === option.value}
                onClick={() => edit(() => setDeliveryMode(option.value))}
                disabled={saving}
              >
                {option.label}
              </button>
            ))}
          </div>
          <p className="mini-help mini-help--center">
            {DELIVERY_OPTIONS.find((option) => option.value === deliveryMode)?.hint}
          </p>
        </section>

        <section className="mini-card" aria-labelledby="mini-scenario-title">
          <h2 id="mini-scenario-title" className="mini-card__title">Сценарий по умолчанию</h2>
          <p className="mini-card__hint">Подставляется при запуске проверки в приложении.</p>
          <div className="mini-field">
            <label className="mini-label" htmlFor="mini-default-scenario">Сценарий</label>
            <div className="mini-select-wrap">
              <select
                id="mini-default-scenario"
                className="mini-select"
                value={scenario}
                onChange={(event) => edit(() => setScenario(event.target.value))}
                disabled={saving || scenarios.length === 0}
              >
                <option value="">Не выбран</option>
                {scenarios.map((item) => (
                  <option value={item.id} key={item.id}>{item.name}</option>
                ))}
              </select>
            </div>
          </div>
          {scenarioName && <p className="mini-help">Сейчас: {scenarioName}</p>}
          {scenarios.length === 0 && (
            <p className="mini-help">Сценариев пока нет — создайте их в веб-кабинете.</p>
          )}
        </section>

        {error && <Alert tone="error">{error}</Alert>}
        {savedNotice && !dirty && (
          <Alert tone="success">
            <span className="mini-inline"><Check size={15} /> Настройки сохранены</span>
          </Alert>
        )}

        <button
          type="submit"
          className="mini-btn mini-btn--primary mini-btn--block"
          disabled={!dirty || saving}
          aria-busy={saving}
        >
          {saving ? <Spinner /> : <Save size={17} />}
          {saving ? 'Сохраняем…' : dirty ? 'Сохранить настройки' : 'Всё сохранено'}
        </button>
      </form>

      <section className="mini-card mini-account" aria-labelledby="mini-account-title">
        <h2 id="mini-account-title" className="mini-card__title">Аккаунт</h2>
        <div className="mini-account__row">
          <span className="mini-account__value">{displayName}</span>
          <span className="mini-account__tag">Telegram</span>
        </div>
        <p className="mini-help">Сессия хранится только в памяти и закрывается вместе с приложением.</p>
      </section>
    </div>
  )
}
