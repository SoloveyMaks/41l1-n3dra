import { ClipboardList, Home, Settings } from 'lucide-react'

export type MiniTab = 'home' | 'runs' | 'settings'

const TABS: Array<{ id: MiniTab; label: string; icon: typeof Home }> = [
  { id: 'home', label: 'Главная', icon: Home },
  { id: 'runs', label: 'Проверки', icon: ClipboardList },
  { id: 'settings', label: 'Настройки', icon: Settings },
]

export function TabBar({ tab, onChange }: { tab: MiniTab; onChange: (tab: MiniTab) => void }) {
  return (
    <nav className="mini-tabbar" aria-label="Разделы приложения">
      <div className="mini-tabbar__inner">
        {TABS.map(({ id, label, icon: Icon }) => (
          <button
            type="button"
            key={id}
            className="mini-tab"
            aria-current={tab === id ? 'page' : undefined}
            onClick={() => onChange(id)}
          >
            <Icon size={20} strokeWidth={2} aria-hidden="true" />
            <span>{label}</span>
          </button>
        ))}
      </div>
    </nav>
  )
}
