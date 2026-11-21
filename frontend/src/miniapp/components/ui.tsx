import type { ReactNode } from 'react'
import { AlertCircle, CheckCircle2, Info, Loader2, ShieldCheck } from 'lucide-react'
import type { Tone } from '../status'

export function Spinner({ size = 16 }: { size?: number }) {
  return <Loader2 size={size} className="mini-spin" aria-hidden="true" />
}

export function BrandHeader({ subtitle }: { subtitle?: string }) {
  return (
    <header className="mini-brand">
      <div className="mini-brand__mark" aria-hidden="true">
        <ShieldCheck size={21} strokeWidth={2.2} />
      </div>
      <div className="mini-brand__text">
        <div className="mini-brand__name">NEDRA</div>
        <div className="mini-brand__sub">{subtitle || 'Проверки безопасности репозиториев'}</div>
      </div>
    </header>
  )
}

export function Pill({ tone = 'muted', children, dot = false }: { tone?: Tone; children: ReactNode; dot?: boolean }) {
  return (
    <span className={`mini-pill mini-pill--${tone}`}>
      {dot && <span className="mini-dot" aria-hidden="true" />}
      {children}
    </span>
  )
}

export function Alert({ tone = 'error', children }: { tone?: 'error' | 'success' | 'info'; children: ReactNode }) {
  const Icon = tone === 'success' ? CheckCircle2 : tone === 'info' ? Info : AlertCircle
  return (
    <div className={`mini-alert mini-alert--${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <Icon size={17} aria-hidden="true" />
      <div className="mini-alert__body">{children}</div>
    </div>
  )
}

export function EmptyState({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="mini-empty">
      <div className="mini-empty__icon" aria-hidden="true">
        <ShieldCheck size={22} />
      </div>
      <div className="mini-empty__title">{title}</div>
      {children && <div className="mini-empty__text">{children}</div>}
    </div>
  )
}

export function SkeletonList({ rows = 3 }: { rows?: number }) {
  return (
    <div className="mini-stack" aria-hidden="true">
      {Array.from({ length: rows }, (_, index) => (
        <div className="mini-card mini-skeleton-card" key={index}>
          <div className="mini-skeleton" style={{ width: '42%', height: 12 }} />
          <div className="mini-skeleton" style={{ width: '78%', height: 16 }} />
          <div className="mini-skeleton" style={{ width: '58%', height: 12 }} />
        </div>
      ))}
    </div>
  )
}
