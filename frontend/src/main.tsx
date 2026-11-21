import { Component, StrictMode, type ErrorInfo, type ReactNode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'

class RootErrorBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false }

  static getDerivedStateFromError() {
    return { failed: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Application crashed', error, info)
  }

  render() {
    if (this.state.failed) {
      return (
        <div style={{ minHeight: '100vh', display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: 12, background: '#020617', color: '#e2e8f0', font: '16px/1.5 system-ui, sans-serif', textAlign: 'center', padding: 24 }}>
          <h1 style={{ margin: 0, fontSize: 20 }}>AI Security Workflow</h1>
          <p style={{ margin: 0, color: '#fca5a5' }}>Интерфейс не запустился.</p>
          <button onClick={() => window.location.reload()} style={{ border: 0, borderRadius: 8, background: '#4f46e5', color: '#fff', padding: '10px 16px', cursor: 'pointer' }}>
            Перезагрузить
          </button>
        </div>
      )
    }
    return this.props.children
  }
}

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <RootErrorBoundary>
      <App />
    </RootErrorBoundary>
  </StrictMode>,
)
