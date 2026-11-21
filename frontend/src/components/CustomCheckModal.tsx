import React, { useEffect, useRef, useState } from 'react';
import {
  createCustomCheck,
  listCustomCheckTemplates,
  previewCustomCheck,
  validateCustomCheck,
  type CustomCheckDefinition,
  type CustomCheckPreview,
  type CustomCheckSpec,
  type CustomCheckTemplate,
  type CustomCheckValidation,
  CustomCheckApiError,
} from '../api/customChecks';

interface CustomCheckModalProps {
  isOpen: boolean;
  onClose: () => void;
  onSaved?: (definition: CustomCheckDefinition) => void;
}

const FALLBACK_TEMPLATES: CustomCheckTemplate[] = [
  { id: 'todo_scan', title: 'TODO / FIXME', language: 'python', runner: 'bwrap-python3' },
  { id: 'file_inventory', title: 'Инвентаризация файлов', language: 'python', runner: 'bwrap-python3' },
];

const EMPTY_FORM: CustomCheckSpec = {
  name: '',
  version: '1.0.0',
  language: 'python',
  runner: 'bwrap-python3',
  sourceKind: 'template',
  script: '',
  templateId: 'todo_scan',
  timeoutSeconds: 3,
  memoryMb: 256,
  maxOutputBytes: 16384,
};

function statusLabel(status: string): string {
  switch (status) {
    case 'succeeded':
      return 'контракт выполнен';
    case 'timeout':
      return 'timeout';
    case 'output_limit':
      return 'лимит вывода';
    case 'contract_error':
      return 'нарушен контракт';
    case 'failed':
      return 'nonzero exit';
    default:
      return status || 'ошибка';
  }
}

export const CustomCheckModal: React.FC<CustomCheckModalProps> = ({ isOpen, onClose, onSaved }) => {
  const [form, setForm] = useState<CustomCheckSpec>(EMPTY_FORM);
  const [templates, setTemplates] = useState<CustomCheckTemplate[]>(FALLBACK_TEMPLATES);
  const [preview, setPreview] = useState<CustomCheckPreview | null>(null);
  const [validation, setValidation] = useState<CustomCheckValidation | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saved, setSaved] = useState<CustomCheckDefinition | null>(null);
  const [busy, setBusy] = useState<'preview' | 'validate' | 'save' | null>(null);
  const seqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    if (!isOpen) return;
    const controller = new AbortController();
    listCustomCheckTemplates(controller.signal)
      .then((items) => {
        if (items.length) setTemplates(items);
      })
      .catch(() => {
        // Server-rendered fallback stays usable when the optional endpoint is absent.
      });
    return () => controller.abort();
  }, [isOpen]);

  useEffect(() => {
    if (!isOpen) {
      seqRef.current += 1;
      abortRef.current?.abort();
      setForm(EMPTY_FORM);
      setPreview(null);
      setValidation(null);
      setSaved(null);
      setError(null);
      setBusy(null);
    }
  }, [isOpen]);

  useEffect(() => () => {
    seqRef.current += 1;
    abortRef.current?.abort();
  }, []);

  const updateForm = (next: CustomCheckSpec) => {
    seqRef.current += 1;
    abortRef.current?.abort();
    setForm(next);
    setValidation(null);
    setPreview(null);
    setSaved(null);
    setBusy(null);
    setError(null);
  };

  if (!isOpen) return null;

  const spec = (): CustomCheckSpec => ({
    ...form,
    sourceKind: form.sourceKind,
    script: form.sourceKind === 'script' ? form.script : '',
    templateId: form.sourceKind === 'template' ? form.templateId : null,
  });

  const start = (kind: 'preview' | 'validate' | 'save') => {
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    const seq = seqRef.current + 1;
    seqRef.current = seq;
    setBusy(kind);
    setError(null);
    if (kind !== 'save') setSaved(null);
    return { controller, seq };
  };

  const finish = (seq: number) => {
    if (seq === seqRef.current) setBusy(null);
  };

  const handlePreview = async () => {
    const { controller, seq } = start('preview');
    try {
      const result = await previewCustomCheck(spec(), controller.signal);
      if (seq !== seqRef.current) return;
      setPreview(result);
      setValidation(null);
    } catch (err) {
      if (seq !== seqRef.current) return;
      setError(err instanceof CustomCheckApiError ? err.message : 'Не удалось проверить описание');
    } finally {
      finish(seq);
    }
  };

  const handleValidate = async () => {
    const { controller, seq } = start('validate');
    try {
      const result = await validateCustomCheck(spec(), controller.signal);
      if (seq !== seqRef.current) return;
      setValidation(result);
      setPreview(result.preview);
      if (!result.valid) {
        setError(result.run.diagnostics[0]?.message || 'Проверка не прошла validate');
      }
    } catch (err) {
      if (seq !== seqRef.current) return;
      setError(err instanceof CustomCheckApiError ? err.message : 'Не удалось выполнить validate');
    } finally {
      finish(seq);
    }
  };

  const handleSave = async () => {
    if (!validation?.valid) {
      setError('Сначала выполните validate: в палитру попадает только проверенная проверка');
      return;
    }
    const { controller, seq } = start('save');
    try {
      const result = await createCustomCheck(spec(), controller.signal);
      if (seq !== seqRef.current) return;
      setSaved(result);
      onSaved?.(result);
    } catch (err) {
      if (seq !== seqRef.current) return;
      setError(err instanceof CustomCheckApiError ? err.message : 'Не удалось сохранить проверку');
    } finally {
      finish(seq);
    }
  };

  const run = validation?.run;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-950/75 p-3" role="presentation">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="custom-check-title"
        className="max-h-[92vh] w-full max-w-2xl overflow-y-auto rounded-2xl border border-slate-700 bg-slate-900 p-5 text-slate-100 shadow-2xl"
      >
        <div className="mb-4 flex items-start justify-between gap-3">
          <div>
            <h2 id="custom-check-title" className="text-base font-semibold">Своя проверка</h2>
            <p className="mt-1 text-xs text-slate-400">
              Проверка запускается изолированно: без сети, секретов сервера и записи в проект.
            </p>
          </div>
          <button
            type="button"
            aria-label="Закрыть"
            onClick={onClose}
            className="rounded-lg border border-slate-700 px-2.5 py-1 text-xs text-slate-400 hover:text-white"
          >
            Закрыть
          </button>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-slate-300">
            Название
            <input
              aria-label="Название проверки"
              value={form.name}
              onChange={(event) => updateForm({ ...form, name: event.target.value })}
              placeholder="Например, TODO в Kotlin"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          <label className="text-xs text-slate-300">
            Версия
            <input
              aria-label="Версия проверки"
              value={form.version}
              onChange={(event) => updateForm({ ...form, version: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
        </div>

        <div className="mt-3 grid gap-3 sm:grid-cols-2">
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="text-slate-500">Язык</div>
            <div className="mt-1 font-mono text-slate-200">Python 3 · /usr/bin/python3</div>
          </div>
          <div className="rounded-lg border border-slate-800 bg-slate-950/60 p-3 text-xs">
            <div className="text-slate-500">Runner</div>
            <div className="mt-1 font-mono text-slate-200">bwrap-python3 · без сети</div>
          </div>
        </div>

        <div className="mt-4 flex flex-wrap gap-4 text-xs text-slate-300">
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="custom-check-source"
              checked={form.sourceKind === 'template'}
              onChange={() => updateForm({ ...form, sourceKind: 'template', templateId: form.templateId || 'todo_scan' })}
            />
            Встроенный шаблон
          </label>
          <label className="flex items-center gap-2">
            <input
              type="radio"
              name="custom-check-source"
              checked={form.sourceKind === 'script'}
              onChange={() => updateForm({ ...form, sourceKind: 'script' })}
            />
            Свой Python-скрипт
          </label>
        </div>

        {form.sourceKind === 'template' ? (
          <label className="mt-3 block text-xs text-slate-300">
            Шаблон
            <select
              aria-label="Встроенный шаблон проверки"
              value={form.templateId || ''}
              onChange={(event) => updateForm({ ...form, templateId: event.target.value })}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            >
              {templates.map((item) => (
                <option key={item.id} value={item.id}>{item.title}</option>
              ))}
            </select>
          </label>
        ) : (
          <label className="mt-3 block text-xs text-slate-300">
            Скрипт (stdout — JSON-контракт custom-check-result/1)
            <textarea
              aria-label="Python-скрипт проверки"
              value={form.script}
              onChange={(event) => updateForm({ ...form, script: event.target.value })}
              rows={10}
              spellCheck={false}
              placeholder="import json; print(json.dumps({'schemaVersion': 'custom-check-result/1', 'findings': []}))"
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 font-mono text-xs text-slate-100"
            />
          </label>
        )}

        <div className="mt-3 grid gap-3 sm:grid-cols-3">
          <label className="text-xs text-slate-300">
            Timeout, сек
            <input
              aria-label="Timeout в секундах"
              type="number"
              min={1}
              max={10}
              value={form.timeoutSeconds}
              onChange={(event) => updateForm({ ...form, timeoutSeconds: Number(event.target.value) })}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          <label className="text-xs text-slate-300">
            Память, МБ
            <input
              aria-label="Лимит памяти в мегабайтах"
              type="number"
              min={64}
              max={512}
              value={form.memoryMb}
              onChange={(event) => updateForm({ ...form, memoryMb: Number(event.target.value) })}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
          <label className="text-xs text-slate-300">
            Вывод, байт
            <input
              aria-label="Лимит вывода в байтах"
              type="number"
              min={1024}
              max={65536}
              value={form.maxOutputBytes}
              onChange={(event) => updateForm({ ...form, maxOutputBytes: Number(event.target.value) })}
              className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-slate-100"
            />
          </label>
        </div>

        <div className="mt-4 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={handlePreview}
            disabled={busy !== null}
            className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-200 hover:bg-slate-800 disabled:opacity-40"
          >
            {busy === 'preview' ? 'Проверяем…' : 'Проверить описание'}
          </button>
          <button
            type="button"
            onClick={handleValidate}
            disabled={busy !== null}
            className="rounded-lg bg-indigo-600 px-3 py-2 text-xs font-semibold text-white hover:bg-indigo-500 disabled:opacity-40"
          >
            {busy === 'validate' ? 'Запускаем в sandbox…' : 'Проверить контракт в sandbox'}
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={busy !== null || !validation?.valid}
            className="rounded-lg border border-emerald-700 bg-emerald-950/50 px-3 py-2 text-xs font-semibold text-emerald-300 hover:bg-emerald-900/60 disabled:opacity-40"
          >
            {busy === 'save' ? 'Сохраняем…' : 'Добавить в палитру после проверки'}
          </button>
        </div>

        {error && (
          <div role="alert" className="mt-3 rounded-lg border border-rose-800 bg-rose-950/40 p-3 text-xs text-rose-200">
            {error}
          </div>
        )}

        {saved && (
          <div className="mt-3 rounded-lg border border-emerald-800 bg-emerald-950/40 p-3 text-xs text-emerald-200">
            Проверка «{saved.name}» v{saved.version} добавлена в палитру. Запуск на проекте — только вручную.
          </div>
        )}

        {run && (
          <div className="mt-4 space-y-3 rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-xs">
            <div className="flex flex-wrap items-center gap-3">
              <span className={`rounded-full px-2 py-1 font-semibold ${
                run.status === 'succeeded'
                  ? 'bg-emerald-950 text-emerald-300'
                  : 'bg-rose-950 text-rose-300'
              }`}>
                {statusLabel(run.status)}
              </span>
              <span className="text-slate-400">exit {run.exitCode ?? '—'}</span>
              <span className="text-slate-400">{run.durationMs} мс</span>
              <span className="text-slate-500">
                лимиты: {run.limits.timeoutSeconds ?? form.timeoutSeconds}с / {run.limits.memoryMb ?? form.memoryMb}МБ / {run.limits.maxOutputBytes ?? form.maxOutputBytes}Б
              </span>
            </div>
            {run.summary && <div className="text-slate-300">Сводка: {run.summary}</div>}
            <div className="text-slate-500">stdout redacted</div>
            <pre className="max-h-32 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/30 p-2 text-[11px] text-slate-300">
              {run.stdout || '(пусто)'}
            </pre>
            {run.stderr && (
              <>
                <div className="text-slate-500">stderr redacted</div>
                <pre className="max-h-24 overflow-auto whitespace-pre-wrap break-all rounded-lg bg-black/30 p-2 text-[11px] text-rose-200">
                  {run.stderr}
                </pre>
              </>
            )}
            {run.diagnostics.length > 0 && (
              <ul className="list-inside list-disc space-y-1 text-amber-200">
                {run.diagnostics.map((item, index) => (
                  <li key={`${item.code || 'diag'}-${index}`}>{item.message || item.code}</li>
                ))}
              </ul>
            )}
            <div className="text-slate-500">
              Найдено custom-фактов: {run.findings.length}. Это отдельный диагностический поток, не canonical findings.
            </div>
          </div>
        )}

        {preview && !run && (
          <div className="mt-3 rounded-lg border border-slate-800 bg-slate-950/50 p-3 text-xs text-slate-400">
            <div>Контракт входа: <span className="font-mono text-slate-300">{preview.inputContract}</span></div>
            <div>Контракт выхода: <span className="font-mono text-slate-300">{preview.outputContract}</span></div>
            <div className="mt-2 text-slate-500">{preview.warnings.join(' ')}</div>
          </div>
        )}
      </div>
    </div>
  );
};
