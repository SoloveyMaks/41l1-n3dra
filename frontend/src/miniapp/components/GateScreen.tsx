import { useState, type FormEvent } from 'react'
import { ExternalLink, KeyRound, Link2Off, RotateCcw, ShieldCheck, Smartphone, TimerOff } from 'lucide-react'
import { Alert, BrandHeader, Spinner } from './ui'

export type GateKind = 'loading' | 'outside-sdk' | 'outside-init' | 'unbound' | 'invalid' | 'error'

interface GateScreenProps {
  kind: GateKind
  message?: string
  onRetry: () => void
  onOpenCabinet: () => void
  onReopen: () => void
  binding?: boolean
  bindError?: string | null
  onBind?: (code: string) => void
}

function BindForm({
  binding,
  error,
  onBind,
  onOpenCabinet,
}: {
  binding: boolean
  error: string | null
  onBind: (code: string) => void
  onOpenCabinet: () => void
}) {
  const [code, setCode] = useState('')

  function submit(event: FormEvent) {
    event.preventDefault()
    const value = code.trim()
    if (!value || binding) return
    onBind(value)
  }

  return (
    <form className="mini-form mini-bind" onSubmit={submit} noValidate>
      <div className="mini-field">
        <label className="mini-label" htmlFor="mini-bind-code">Код из кабинета</label>
        <div className="mini-input-wrap">
          <KeyRound size={17} className="mini-input-icon" aria-hidden="true" />
          <input
            id="mini-bind-code"
            className="mini-input mini-input--with-icon"
            type="text"
            inputMode="text"
            autoComplete="one-time-code"
            autoCapitalize="none"
            spellCheck={false}
            enterKeyHint="send"
            maxLength={128}
            placeholder="Введите код привязки"
            value={code}
            onChange={(event) => setCode(event.target.value)}
            disabled={binding}
            aria-describedby="mini-bind-hint"
          />
        </div>
        <p id="mini-bind-hint" className="mini-help">
          Откройте веб-кабинет NEDRA → Telegram и создайте код привязки. Введите его здесь. Код действует один раз.
        </p>
      </div>

      {error && <Alert tone="error">{error}</Alert>}

      <button
        type="submit"
        className="mini-btn mini-btn--primary mini-btn--block"
        disabled={binding || !code.trim()}
        aria-busy={binding}
      >
        {binding ? <Spinner /> : <ShieldCheck size={17} aria-hidden="true" />}
        {binding ? 'Привязываем…' : 'Привязать аккаунт'}
      </button>
      <button type="button" className="mini-btn mini-btn--ghost mini-btn--block" onClick={onOpenCabinet} disabled={binding}>
        <ExternalLink size={16} /> Открыть веб-кабинет
      </button>
    </form>
  )
}

export function GateScreen({ kind, message, onRetry, onOpenCabinet, onReopen, binding = false, bindError = null, onBind }: GateScreenProps) {
  if (kind === 'loading') {
    return (
      <div className="mini-gate">
        <BrandHeader />
        <div className="mini-card mini-gate__card">
          <div className="mini-pending">
            <Spinner size={20} />
            <span>Проверяем доступ…</span>
          </div>
          <div className="mini-skeleton" style={{ width: '72%', height: 12 }} />
          <div className="mini-skeleton" style={{ width: '48%', height: 12 }} />
        </div>
      </div>
    )
  }

  if (kind === 'outside-sdk' || kind === 'outside-init') {
    const noInitData = kind === 'outside-init'
    return (
      <div className="mini-gate">
        <BrandHeader />
        <div className="mini-card mini-gate__card">
          <div className="mini-gate__icon" aria-hidden="true">
            <Smartphone size={24} />
          </div>
          <h1 className="mini-gate__title">Откройте приложение из Telegram</h1>
          <p className="mini-gate__text">
            {noInitData
              ? 'Telegram не передал данные для входа. Закройте приложение и снова откройте его из бота NEDRA.'
              : 'Это мобильное приложение работает внутри Telegram. Найдите бота NEDRA, отправьте /start и нажмите «Открыть приложение».'}
          </p>
          <ol className="mini-steps">
            <li>Откройте чат с ботом NEDRA в Telegram.</li>
            <li>Нажмите кнопку запуска приложения.</li>
            <li>Если кнопки нет — отправьте /start.</li>
          </ol>
          <p className="mini-note">
            Логин и пароль здесь не запрашиваются. Вход возможен только через Telegram.
          </p>
          <button type="button" className="mini-btn mini-btn--primary mini-btn--block" onClick={onRetry}>
            <RotateCcw size={17} /> Попробовать снова
          </button>
          <button type="button" className="mini-btn mini-btn--ghost mini-btn--block" onClick={onOpenCabinet}>
            <ExternalLink size={16} /> Открыть веб-кабинет
          </button>
        </div>
      </div>
    )
  }

  if (kind === 'unbound') {
    return (
      <div className="mini-gate">
        <BrandHeader />
        <div className="mini-card mini-gate__card">
          <div className="mini-gate__icon" aria-hidden="true">
            <Link2Off size={24} />
          </div>
          <h1 className="mini-gate__title">Привяжите Telegram</h1>
          <p className="mini-gate__text">
            Введите код привязки — после этого проверки и настройки откроются в приложении.
          </p>
          <BindForm
            binding={binding}
            error={bindError}
            onBind={onBind ?? onRetry}
            onOpenCabinet={onOpenCabinet}
          />
        </div>
      </div>
    )
  }

  if (kind === 'invalid') {
    return (
      <div className="mini-gate">
        <BrandHeader />
        <div className="mini-card mini-gate__card">
          <div className="mini-gate__icon" aria-hidden="true">
            <TimerOff size={24} />
          </div>
          <h1 className="mini-gate__title">Сессия истекла</h1>
          <p className="mini-gate__text">
            Доступ к проверкам нужно открыть заново из Telegram — старый вход больше не действует.
          </p>
          <button type="button" className="mini-btn mini-btn--primary mini-btn--block" onClick={onReopen}>
            <RotateCcw size={17} /> Открыть заново
          </button>
          <button type="button" className="mini-btn mini-btn--ghost mini-btn--block" onClick={onRetry}>
            Обновить страницу
          </button>
        </div>
      </div>
    )
  }

  return (
    <div className="mini-gate">
      <BrandHeader />
      <div className="mini-card mini-gate__card">
        <h1 className="mini-gate__title">Не удалось открыть приложение</h1>
        <Alert tone="error">{message || 'Сервис не ответил. Попробуйте ещё раз.'}</Alert>
        <button type="button" className="mini-btn mini-btn--primary mini-btn--block" onClick={onRetry}>
          <RotateCcw size={17} /> Повторить
        </button>
        <button type="button" className="mini-btn mini-btn--ghost mini-btn--block" onClick={onOpenCabinet}>
          <ExternalLink size={16} /> Открыть веб-кабинет
        </button>
      </div>
    </div>
  )
}
