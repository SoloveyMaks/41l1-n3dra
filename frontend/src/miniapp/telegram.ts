// Тонкая обёртка над официальным Telegram WebApp SDK. Скрипт грузится только
// на /miniapp, чтобы основной интерфейс не зависел от внешнего домена.
const SDK_SRC = 'https://telegram.org/js/telegram-web-app.js'

export interface TelegramThemeParams {
  bg_color?: string
  text_color?: string
  hint_color?: string
  link_color?: string
  button_color?: string
  button_text_color?: string
  secondary_bg_color?: string
  header_bg_color?: string
  accent_text_color?: string
  section_bg_color?: string
  section_separator_color?: string
  destructive_text_color?: string
  [key: string]: string | undefined
}

export interface TelegramWebApp {
  initData: string
  // Личность из initDataUnsafe не используется для авторизации: вход решает
  // только подписанный initData на бэкенде.
  initDataUnsafe?: { user?: { id?: number; username?: string } }
  colorScheme?: 'light' | 'dark'
  platform?: string
  themeParams?: TelegramThemeParams
  viewportStableHeight?: number
  isExpanded?: boolean
  version?: string
  ready?: () => void
  expand?: () => void
  close?: () => void
  openLink?: (url: string) => void
  setHeaderColor?: (color: string) => void
  setBackgroundColor?: (color: string) => void
  onEvent?: (event: string, handler: () => void) => void
  offEvent?: (event: string, handler: () => void) => void
}

type TelegramHost = { Telegram?: { WebApp?: TelegramWebApp } }

export function getTelegramWebApp(): TelegramWebApp | null {
  if (typeof window === 'undefined') return null
  return (window as unknown as TelegramHost).Telegram?.WebApp ?? null
}

// В обычном браузере нет ни WebView-моста, ни tgWebAppData в адресе. Это тот
// же признак, по которому официальный SDK выставляет platform='unknown', но
// проверка локальная: не ждём внешний скрипт, чтобы честно сказать «откройте
// из Telegram».
function telegramHostHint(): boolean {
  if (typeof window === 'undefined') return false
  const bridge = window as unknown as { TelegramWebviewProxy?: unknown; TelegramWebviewProxyProto?: unknown }
  if (bridge.TelegramWebviewProxy || bridge.TelegramWebviewProxyProto) return true
  const location = window.location
  return /(?:^|[#&?])tgWebApp(?:Data|Version|Platform|ThemeParams)=/.test(`${location.search}${location.hash}`)
}

let sdkPromise: Promise<TelegramWebApp | null> | null = null

export function loadTelegramSdk(): Promise<TelegramWebApp | null> {
  if (sdkPromise) return sdkPromise
  sdkPromise = new Promise((resolve) => {
    if (typeof document === 'undefined') {
      resolve(null)
      return
    }
    const existing = getTelegramWebApp()
    if (existing) {
      resolve(existing)
      return
    }
    if (!telegramHostHint()) {
      resolve(null)
      return
    }
    let settled = false
    const finish = (app: TelegramWebApp | null) => {
      if (settled) return
      settled = true
      resolve(app)
    }
    const script = document.createElement('script')
    script.src = SDK_SRC
    script.async = true
    script.onload = () => finish(getTelegramWebApp())
    script.onerror = () => finish(null)
    document.head.appendChild(script)
    // Старые клиенты могут не вызвать onload; не держим экран в вечной загрузке.
    window.setTimeout(() => finish(getTelegramWebApp()), 8000)
  })
  return sdkPromise
}

export type MiniScheme = 'dark' | 'light'

export interface MiniTheme {
  scheme: MiniScheme
  bg: string
  chrome: string
  vars: Record<string, string>
}

const DARK_BG = '#050a18'
const LIGHT_BG = '#f4f6fd'

function luminance(hex?: string): number | null {
  if (!hex) return null
  const match = /^#([0-9a-f]{6})$/i.exec(hex.trim())
  if (!match) return null
  const value = match[1]
  const r = parseInt(value.slice(0, 2), 16) / 255
  const g = parseInt(value.slice(2, 4), 16) / 255
  const b = parseInt(value.slice(4, 6), 16) / 255
  return 0.2126 * r + 0.7152 * g + 0.0722 * b
}

function resolvedScheme(app: TelegramWebApp | null): MiniScheme {
  if (app?.colorScheme === 'light' || app?.colorScheme === 'dark') return app.colorScheme
  const fromParams = luminance(app?.themeParams?.bg_color)
  if (fromParams !== null) return fromParams > 0.55 ? 'light' : 'dark'
  return 'dark'
}

// themeParams отдаём в CSS как --tg-*; фирменные цвета NEDRA остаются поверх,
// поэтому приложение не превращается в серую копию чужого клиента.
export function deriveTheme(app: TelegramWebApp | null): MiniTheme {
  const scheme = resolvedScheme(app)
  const params = app?.themeParams ?? {}
  const vars: Record<string, string> = {}
  const map: Array<[string, keyof TelegramThemeParams]> = [
    ['--tg-bg', 'bg_color'],
    ['--tg-text', 'text_color'],
    ['--tg-hint', 'hint_color'],
    ['--tg-link', 'link_color'],
    ['--tg-button', 'button_color'],
    ['--tg-button-text', 'button_text_color'],
    ['--tg-secondary-bg', 'secondary_bg_color'],
    ['--tg-header-bg', 'header_bg_color'],
    ['--tg-accent-text', 'accent_text_color'],
    ['--tg-section-bg', 'section_bg_color'],
    ['--tg-separator', 'section_separator_color'],
    ['--tg-destructive', 'destructive_text_color'],
  ]
  for (const [cssVar, key] of map) {
    const value = params[key]
    if (typeof value === 'string' && value.trim()) vars[cssVar] = value.trim()
  }

  const fallbackBg = scheme === 'light' ? LIGHT_BG : DARK_BG
  const telegramBg = typeof params.bg_color === 'string' && params.bg_color.trim() ? params.bg_color.trim() : fallbackBg
  vars['--mini-bg'] = telegramBg
  vars['--mini-text'] = typeof params.text_color === 'string' && params.text_color.trim()
    ? params.text_color.trim()
    : scheme === 'light' ? '#12182f' : '#eef2ff'
  vars['--mini-muted'] = typeof params.hint_color === 'string' && params.hint_color.trim()
    ? params.hint_color.trim()
    : scheme === 'light' ? '#5b6787' : '#9aa7c7'

  if (typeof app?.viewportStableHeight === 'number' && app.viewportStableHeight > 0) {
    vars['--mini-viewport-height'] = `${Math.round(app.viewportStableHeight)}px`
  }

  return {
    scheme,
    bg: telegramBg,
    chrome: scheme === 'light' ? LIGHT_BG : DARK_BG,
    vars,
  }
}

export function applyTelegramChrome(app: TelegramWebApp | null, theme: MiniTheme) {
  if (!app) return
  try {
    app.setBackgroundColor?.(theme.bg)
  } catch {
    // Клиент старой версии; тема всё равно применена через CSS.
  }
  try {
    app.setHeaderColor?.(theme.chrome)
  } catch {
    // То же: не ломаем приложение из-за неподдерживаемого метода.
  }
}

export function openTelegramLink(app: TelegramWebApp | null, url: string) {
  if (app?.openLink) {
    try {
      app.openLink(url)
      return
    } catch {
      // Падаем в обычное открытие ниже.
    }
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}
