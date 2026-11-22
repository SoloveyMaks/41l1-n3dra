import React, { useEffect, useState } from 'react'
import { Copy, Library, Loader2, Upload, X } from 'lucide-react'
import {
  copyWorkflowTemplate,
  listWorkflowTemplates,
  publishWorkflowTemplate,
  type WorkflowTemplateSummary,
} from '../api/workflowLibrary'

interface WorkflowLibraryModalProps {
  open: boolean
  workflowId?: string
  onClose: () => void
  onCopied: (workflowId: string) => void
}

export const WorkflowLibraryModal: React.FC<WorkflowLibraryModalProps> = ({ open, workflowId, onClose, onCopied }) => {
  const [items, setItems] = useState<WorkflowTemplateSummary[]>([])
  const [loading, setLoading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [publishing, setPublishing] = useState(false)
  const [error, setError] = useState('')

  useEffect(() => {
    if (!open) return
    let cancelled = false
    setLoading(true)
    setError('')
    listWorkflowTemplates()
      .then((payload) => {
        if (!cancelled) setItems(payload.items || [])
      })
      .catch((reason: unknown) => {
        if (!cancelled) setError(reason instanceof Error ? reason.message : 'Не удалось загрузить библиотеку')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => { cancelled = true }
  }, [open])

  if (!open) return null

  const publish = async () => {
    if (!workflowId || publishing) return
    setPublishing(true)
    setError('')
    try {
      await publishWorkflowTemplate(workflowId)
      const payload = await listWorkflowTemplates()
      setItems(payload.items || [])
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Не удалось опубликовать шаблон')
    } finally {
      setPublishing(false)
    }
  }

  const copy = async (templateId: string) => {
    if (busyId) return
    setBusyId(templateId)
    setError('')
    try {
      const result = await copyWorkflowTemplate(templateId)
      onCopied(result.workflowId)
      onClose()
    } catch (reason: unknown) {
      setError(reason instanceof Error ? reason.message : 'Не удалось скопировать шаблон')
    } finally {
      setBusyId(null)
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/80 p-4">
      <div className="flex max-h-[85vh] w-full max-w-2xl flex-col overflow-hidden rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
        <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
          <div className="flex items-center gap-2 text-sm font-semibold text-slate-100">
            <Library size={16} />
            Библиотека шаблонов
          </div>
          <button type="button" onClick={onClose} aria-label="Закрыть библиотеку" className="rounded p-1 text-slate-400 hover:bg-slate-800 hover:text-slate-100">
            <X size={16} />
          </button>
        </div>

        <div className="flex-1 overflow-y-auto p-4">
          {workflowId && (
            <button
              type="button"
              onClick={() => void publish()}
              disabled={publishing}
              className="mb-3 flex w-full items-center justify-center gap-2 rounded-lg border border-emerald-800 bg-emerald-950/60 px-3 py-2 text-xs font-medium text-emerald-200 hover:bg-emerald-900 disabled:opacity-60"
            >
              {publishing ? <Loader2 size={14} className="animate-spin" /> : <Upload size={14} />}
              Опубликовать текущий сценарий
            </button>
          )}
          {error && <p className="mb-3 rounded border border-rose-900 bg-rose-950/40 p-2 text-xs text-rose-200">{error}</p>}
          {loading && <p className="text-xs text-slate-400">Загрузка…</p>}
          {!loading && !items.length && !error && (
            <p className="text-xs text-slate-400">Пока нет опубликованных шаблонов.</p>
          )}
          <ul className="space-y-2">
            {items.map((item) => (
              <li key={item.id} className="rounded-lg border border-slate-800 bg-slate-950/40 p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate text-sm font-semibold text-slate-100">{item.name}</p>
                    <p className="mt-0.5 text-[11px] text-slate-400">
                      {item.nodeCount} блоков{item.publishedAt ? ` · ${new Date(item.publishedAt).toLocaleString('ru-RU')}` : ''}
                    </p>
                    {item.description && <p className="mt-1 text-xs text-slate-300">{item.description}</p>}
                  </div>
                  <button
                    type="button"
                    onClick={() => void copy(item.id)}
                    disabled={busyId !== null}
                    className="flex shrink-0 items-center gap-1 rounded border border-slate-700 bg-slate-800 px-2 py-1 text-xs text-slate-200 hover:bg-slate-700 disabled:opacity-60"
                  >
                    {busyId === item.id ? <Loader2 size={12} className="animate-spin" /> : <Copy size={12} />}
                    Скопировать
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </div>
  )
}
