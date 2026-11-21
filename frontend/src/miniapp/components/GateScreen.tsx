import { ExternalLink, Link2Off, RotateCcw, ShieldCheck, Smartphone, TimerOff } from 'lucide-react'
import { Alert, BrandHeader, Spinner } from './ui'

export type GateKind = 'loading' | 'outside-sdk' | 'outside-init' | 'unbound' | 'invalid' | 'error'

interface GateScreenProps {
  kind: GateKind
  message?: string
  onRetry: () => void
  onOpenCabinet: () => void
  onReopen: () => void
}

export function GateScreen({ kind, message, onRetry, onOpenCabinet, onReopen }: GateScreenProps) {
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
          {noInitData && (
            <button type="button" className="mini-btn mini-btn--primary mini-btn--block" onClick={onRetry}>
              <RotateCcw size={17} /> Попробовать снова
            </button>
          )}
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
          <h1 className="mini-gate__title">Telegram не привязан</h1>
          <p className="mini-gate__text">
            Этот аккаунт ещё не связан с профилем NEDRA. Привязка выполняется вручную в веб-кабинете.
          </p>
          <ol className="mini-steps">
            <li>Откройте веб-кабинет NEDRA.</li>
            <li>В настройках Telegram привяжите этот аккаунт.</li>
            <li>Вернитесь сюда и нажмите «Проверить снова».</li>
          </ol>
          <p className="mini-note">Автоматической привязки нет.</p>
          <button type="button" className="mini-btn mini-btn--primary mini-btn--block" onClick={onRetry}>
            <ShieldCheck size={17} /> Проверить снова
          </button>
          <button type="button" className="mini-btn mini-btn--ghost mini-btn--block" onClick={onOpenCabinet}>
            <ExternalLink size={16} /> Открыть веб-кабинет
          </button>
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
