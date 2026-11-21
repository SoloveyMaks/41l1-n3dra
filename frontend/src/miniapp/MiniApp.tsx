import { useCallback, useEffect, useRef, useState, type CSSProperties } from 'react'
import type { RunSummary, TelegramDeliveryMode, TelegramSettings } from '../types'
import { MiniApi, MiniApiError, combineSignals, errorText } from './api'
import type { MiniBootstrap, MiniReport, MiniRunDetail, MiniRunInput, MiniSession } from './api'
import {
  applyTelegramChrome,
  deriveTheme,
  getTelegramWebApp,
  loadTelegramSdk,
  openTelegramLink,
  type MiniTheme,
  type TelegramWebApp,
} from './telegram'
import { TabBar, type MiniTab } from './components/TabBar'
import { HomeTab } from './components/HomeTab'
import { RunsTab } from './components/RunsTab'
import { RunDetail } from './components/RunDetail'
import { SettingsTab } from './components/SettingsTab'
import { GateScreen, type GateKind } from './components/GateScreen'
import './theme.css'

type View = GateKind | 'ready'

interface SessionHandle {
  api: MiniApi
  controller: AbortController
}

const CABINET_URL = () => new URL('/', window.location.origin).toString()

export default function MiniApp() {
  const [view, setView] = useState<View>('loading')
  const [gateMessage, setGateMessage] = useState('')
  const [bootstrap, setBootstrap] = useState<MiniBootstrap | null>(null)
  const [tab, setTab] = useState<MiniTab>('home')
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null)
  const [sessionKey, setSessionKey] = useState(0)
  const [refreshing, setRefreshing] = useState(false)
  const [syncError, setSyncError] = useState<string | null>(null)
  const [retryTick, setRetryTick] = useState(0)
  const [app, setApp] = useState<TelegramWebApp | null>(null)
  const [theme, setTheme] = useState<MiniTheme>(() => deriveTheme(null))

  const sessionRef = useRef<SessionHandle | null>(null)
  const epochRef = useRef(0)
  const refreshTimerRef = useRef<number | undefined>(undefined)
  const bootstrapSeqRef = useRef(0)

  const invalidateSession = useCallback(() => {
    epochRef.current += 1
    sessionRef.current?.controller.abort()
    sessionRef.current = null
    if (refreshTimerRef.current) {
      window.clearTimeout(refreshTimerRef.current)
      refreshTimerRef.current = undefined
    }
    setBootstrap(null)
    setSyncError(null)
    setSelectedRunId(null)
    setView('invalid')
  }, [])

  const withSession = useCallback(<T,>(fn: (api: MiniApi, signal: AbortSignal) => Promise<T>): Promise<T> => {
    const session = sessionRef.current
    if (!session) return Promise.reject(new MiniApiError('Сессия не активна', 401))
    return fn(session.api, session.controller.signal)
  }, [])

  // Авторизация: только подписанный initData уходит на бэкенд. Токен остаётся
  // в памяти MiniApi; ни localStorage, ни URL его не видят.
  useEffect(() => {
    const epoch = ++epochRef.current
    let alive = true
    sessionRef.current?.controller.abort()
    sessionRef.current = null
    if (refreshTimerRef.current) window.clearTimeout(refreshTimerRef.current)
    setBootstrap(null)
    setSelectedRunId(null)
    setSyncError(null)
    setRefreshing(false)
    setView('loading')

    void (async () => {
      const webApp = await loadTelegramSdk()
      if (!alive || epoch !== epochRef.current) return
      setApp(webApp)
      if (!webApp || webApp.platform === 'unknown' || (!webApp.platform && !webApp.initData)) {
        setView('outside-sdk')
        return
      }
      if (!webApp.initData) {
        setView('outside-init')
        return
      }
      try {
        webApp.ready?.()
        webApp.expand?.()
      } catch {
        // Не критично: приложение работает и без этих вызовов.
      }

      const controller = new AbortController()
      const api = new MiniApi()
      try {
        const session: MiniSession = await api.authorize(webApp.initData, controller.signal)
        if (!alive || epoch !== epochRef.current) return
        api.setToken(session.token)
        sessionRef.current = { api, controller }
        const next = await api.bootstrap(controller.signal)
        if (!alive || epoch !== epochRef.current) return
        setBootstrap(next)
        setSessionKey(epoch)
        setView('ready')

        const armRefresh = (expiresAt: number) => {
          const delay = Math.max(5_000, expiresAt * 1000 - Date.now() - 60_000)
          refreshTimerRef.current = window.setTimeout(async () => {
            if (!alive || epoch !== epochRef.current) return
            try {
              const renewed = await api.authorize(webApp.initData, controller.signal)
              if (!alive || epoch !== epochRef.current) return
              api.setToken(renewed.token)
              armRefresh(renewed.expiresAt)
            } catch (error) {
              if (!alive || controller.signal.aborted || epoch !== epochRef.current) return
              if (error instanceof MiniApiError && error.status === 401) {
                sessionRef.current?.controller.abort()
                sessionRef.current = null
                setBootstrap(null)
                setView('invalid')
              } else {
                armRefresh(Date.now() / 1000 + 90)
              }
            }
          }, delay)
        }
        armRefresh(session.expiresAt)
      } catch (error) {
        if (!alive || epoch !== epochRef.current || controller.signal.aborted) return
        controller.abort()
        sessionRef.current = null
        if (error instanceof MiniApiError && error.status === 409 && error.code === 'TELEGRAM_NOT_BOUND') {
          setView('unbound')
          return
        }
        if (error instanceof MiniApiError && error.status === 401) {
          setView('invalid')
          return
        }
        setGateMessage(errorText(error))
        setView('error')
      }
    })()

    return () => {
      alive = false
      sessionRef.current?.controller.abort()
      sessionRef.current = null
      if (refreshTimerRef.current) {
        window.clearTimeout(refreshTimerRef.current)
        refreshTimerRef.current = undefined
      }
    }
  }, [retryTick])

  // Тема Telegram: themeParams в CSS-переменные, переключение на лету.
  useEffect(() => {
    const apply = () => {
      const next = deriveTheme(app)
      setTheme(next)
      applyTelegramChrome(app, next)
    }
    apply()
    app?.onEvent?.('themeChanged', apply)
    app?.onEvent?.('viewportChanged', apply)
    return () => {
      app?.offEvent?.('themeChanged', apply)
      app?.offEvent?.('viewportChanged', apply)
    }
  }, [app])

  useEffect(() => {
    const html = document.documentElement
    const body = document.body
    html.classList.add('mini-document')
    body.classList.add('mini-document')
    return () => {
      html.classList.remove('mini-document')
      body.classList.remove('mini-document')
    }
  }, [])

  useEffect(() => {
    const previous = document.body.style.backgroundColor
    document.documentElement.style.backgroundColor = theme.bg
    document.body.style.backgroundColor = theme.bg
    return () => {
      document.body.style.backgroundColor = previous
      document.documentElement.style.backgroundColor = ''
    }
  }, [theme.bg])

  const refreshBootstrap = useCallback(async () => {
    const seq = ++bootstrapSeqRef.current
    setRefreshing(true)
    setSyncError(null)
    try {
      const next = await withSession((api, signal) => api.bootstrap(signal))
      if (seq !== bootstrapSeqRef.current) return
      setBootstrap(next)
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return
      if (error instanceof MiniApiError && error.status === 401) {
        invalidateSession()
        return
      }
      setSyncError(errorText(error))
    } finally {
      if (seq === bootstrapSeqRef.current) setRefreshing(false)
    }
  }, [invalidateSession, withSession])

  const createRun = useCallback(async (input: MiniRunInput): Promise<string> => {
    try {
      const result = await withSession((api, signal) => api.createRun(input, signal))
      setSelectedRunId(result.runId)
      setTab('runs')
      void refreshBootstrap()
      return result.runId
    } catch (error) {
      if (error instanceof MiniApiError && error.status === 401) invalidateSession()
      throw error
    }
  }, [invalidateSession, refreshBootstrap, withSession])

  const loadRun = useCallback(async (runId: string, signal?: AbortSignal): Promise<MiniRunDetail> => {
    try {
      return await withSession((api, sessionSignal) => api.getRun(runId, combineSignals(sessionSignal, signal)))
    } catch (error) {
      if (error instanceof MiniApiError && error.status === 401) invalidateSession()
      throw error
    }
  }, [invalidateSession, withSession])

  const getReport = useCallback(async (runId: string, signal?: AbortSignal): Promise<MiniReport> => {
    try {
      return await withSession((api, sessionSignal) => api.downloadReport(runId, combineSignals(sessionSignal, signal)))
    } catch (error) {
      if (error instanceof MiniApiError && error.status === 401) invalidateSession()
      throw error
    }
  }, [invalidateSession, withSession])

  const saveSettings = useCallback(async (patch: { deliveryMode: TelegramDeliveryMode; scenario: string | null }): Promise<TelegramSettings> => {
    try {
      const next = await withSession((api, signal) => api.updateSettings(patch, signal))
      setBootstrap((current) => (current ? { ...current, settings: next } : current))
      return next
    } catch (error) {
      if (error instanceof MiniApiError && error.status === 401) invalidateSession()
      throw error
    }
  }, [invalidateSession, withSession])

  const handleRunUpdated = useCallback((run: RunSummary) => {
    setBootstrap((current) => {
      if (!current) return current
      const index = current.runs.findIndex((item) => item.id === run.id)
      const runs = index >= 0
        ? current.runs.map((item) => (item.id === run.id ? run : item))
        : [run, ...current.runs]
      return { ...current, runs }
    })
  }, [])

  const openRun = useCallback((runId: string) => {
    setSelectedRunId(runId)
    setTab('runs')
  }, [])

  const changeTab = useCallback((next: MiniTab) => {
    setSelectedRunId(null)
    setTab(next)
  }, [])

  const openCabinet = useCallback(() => {
    openTelegramLink(app ?? getTelegramWebApp(), CABINET_URL())
  }, [app])

  const retry = useCallback(() => setRetryTick((value) => value + 1), [])
  const reloadPage = useCallback(() => window.location.reload(), [])
  const reopen = useCallback(() => {
    const webApp = app ?? getTelegramWebApp()
    if (webApp?.close) {
      try {
        webApp.close()
        return
      } catch {
        // Ниже — обычная перезагрузка.
      }
    }
    window.location.reload()
  }, [app])

  const themeStyle = theme.vars as CSSProperties

  if (view !== 'ready' || !bootstrap) {
    return (
      <div className="mini-app" data-scheme={theme.scheme} style={themeStyle}>
        <div className="mini-shell">
          <GateScreen
            kind={view === 'ready' ? 'loading' : view}
            message={gateMessage}
            onRetry={view === 'outside-init' ? reloadPage : retry}
            onOpenCabinet={openCabinet}
            onReopen={reopen}
          />
        </div>
      </div>
    )
  }

  const displayName = bootstrap.user?.displayName || 'пользователь'

  return (
    <div className="mini-app" data-scheme={theme.scheme} style={themeStyle}>
      <div className="mini-shell">
        {tab === 'home' && (
          <HomeTab
            displayName={displayName}
            runs={bootstrap.runs}
            scenarios={bootstrap.scenarios}
            defaultScenario={bootstrap.settings.scenario}
            refreshing={refreshing}
            syncError={syncError}
            onCreate={createRun}
            onOpenRun={openRun}
            onRefresh={() => void refreshBootstrap()}
          />
        )}
        {tab === 'runs' && (selectedRunId ? (
          <RunDetail
            key={selectedRunId}
            runId={selectedRunId}
            sessionKey={sessionKey}
            onBack={() => setSelectedRunId(null)}
            loadRun={loadRun}
            getReport={getReport}
            onRunUpdated={handleRunUpdated}
          />
        ) : (
          <RunsTab
            runs={bootstrap.runs}
            refreshing={refreshing}
            syncError={syncError}
            onOpenRun={openRun}
            onRefresh={() => void refreshBootstrap()}
          />
        ))}
        {tab === 'settings' && (
          <SettingsTab
            displayName={displayName}
            settings={bootstrap.settings}
            scenarios={bootstrap.scenarios}
            onSave={saveSettings}
          />
        )}
      </div>
      <TabBar tab={tab} onChange={changeTab} />
    </div>
  )
}
