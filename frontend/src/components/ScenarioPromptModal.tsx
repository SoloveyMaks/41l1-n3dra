import React, { useEffect, useRef, useState } from 'react';
import { Loader2, Mic, Sparkles, Square, X } from 'lucide-react';
import {
  ScenarioBuilderApiError,
  requestScenarioPreview,
  type ScenarioDraft,
  type ScenarioPreview,
} from '../api/scenarioBuilder';

interface ScenarioPromptModalProps {
  isOpen: boolean;
  onClose: () => void;
  /** Called only after the user clicks «Применить»; never on preview. */
  onApply: (draft: ScenarioDraft) => void;
  initialPrompt?: string;
}

interface SpeechResultAlternative {
  transcript: string;
}

interface SpeechResult {
  isFinal: boolean;
  0: SpeechResultAlternative;
}

interface SpeechResultList {
  length: number;
  [index: number]: SpeechResult;
}

interface SpeechRecognitionEventLike {
  results: SpeechResultList;
  resultIndex: number;
}

interface SpeechRecognitionErrorLike {
  error?: string;
}

interface SpeechRecognitionLike {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: SpeechRecognitionEventLike) => void) | null;
  onerror: ((event: SpeechRecognitionErrorLike) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
}

type SpeechRecognitionCtor = new () => SpeechRecognitionLike;

function speechCtor(): SpeechRecognitionCtor | null {
  if (typeof window === 'undefined') return null;
  const candidate = window as unknown as {
    SpeechRecognition?: SpeechRecognitionCtor;
    webkitSpeechRecognition?: SpeechRecognitionCtor;
  };
  return candidate.SpeechRecognition || candidate.webkitSpeechRecognition || null;
}

const MIN_PROMPT = 8;
const MAX_PROMPT = 2000;

export const ScenarioPromptModal: React.FC<ScenarioPromptModalProps> = ({
  isOpen,
  onClose,
  onApply,
  initialPrompt = '',
}) => {
  const [prompt, setPrompt] = useState(initialPrompt.slice(0, MAX_PROMPT));
  const [preview, setPreview] = useState<ScenarioPreview | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [listening, setListening] = useState(false);
  const [voiceError, setVoiceError] = useState<string | null>(null);
  const [voiceSupported, setVoiceSupported] = useState(false);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const voiceSeqRef = useRef(0);
  const requestSeqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);

  useEffect(() => {
    setVoiceSupported(Boolean(speechCtor()));
  }, []);

  useEffect(() => {
    if (!isOpen) {
      requestSeqRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      setLoading(false);
      voiceSeqRef.current += 1;
      recognitionRef.current?.abort();
      recognitionRef.current = null;
      setListening(false);
      setPreview(null);
      setError(null);
      setVoiceError(null);
    }
  }, [isOpen]);

  useEffect(() => () => {
    requestSeqRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    voiceSeqRef.current += 1;
    recognitionRef.current?.abort();
    recognitionRef.current = null;
  }, []);

  if (!isOpen) return null;

  const stopVoice = (cancel = false) => {
    if (cancel) {
      voiceSeqRef.current += 1;
      recognitionRef.current?.abort();
      recognitionRef.current = null;
    } else {
      recognitionRef.current?.stop();
    }
    setListening(false);
  };

  const close = () => {
    requestSeqRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    setLoading(false);
    stopVoice(true);
    onClose();
  };

  const startVoice = () => {
    if (listening) {
      stopVoice();
      return;
    }
    const Ctor = speechCtor();
    if (!Ctor) {
      setVoiceSupported(false);
      setVoiceError('Голосовой ввод не поддерживается этим браузером. Введите текст вручную.');
      return;
    }
    setVoiceError(null);
    const voiceSession = voiceSeqRef.current + 1;
    voiceSeqRef.current = voiceSession;
    try {
      const recognition = new Ctor();
      recognition.lang = 'ru-RU';
      recognition.continuous = false;
      recognition.interimResults = false;
      recognition.onresult = (event) => {
        if (voiceSeqRef.current !== voiceSession || recognitionRef.current !== recognition) return;
        const fragments: string[] = [];
        for (let index = event.resultIndex; index < event.results.length; index += 1) {
          const result = event.results[index];
          if (result?.isFinal && result[0]?.transcript) {
            fragments.push(result[0].transcript.trim());
          }
        }
        if (fragments.length) {
          setPrompt((current) => {
            const joined = fragments.join(' ').trim();
            const next = current.trim() ? `${current.trim()} ${joined}` : joined;
            return next.slice(0, MAX_PROMPT);
          });
        }
      };
      recognition.onerror = (event) => {
        if (voiceSeqRef.current !== voiceSession || recognitionRef.current !== recognition) return;
        const code = event.error || 'unknown';
        const message = code === 'not-allowed' || code === 'service-not-allowed'
          ? 'Доступ к микрофону запрещён. Разрешите его в браузере или введите текст вручную.'
          : code === 'no-speech'
            ? 'Речь не распознана. Попробуйте ещё раз или введите текст.'
            : 'Голосовой ввод остановился с ошибкой. Введите текст вручную.';
        setVoiceError(message);
        setListening(false);
        recognitionRef.current = null;
      };
      recognition.onend = () => {
        if (voiceSeqRef.current !== voiceSession || recognitionRef.current !== recognition) return;
        setListening(false);
        recognitionRef.current = null;
      };
      recognitionRef.current = recognition;
      recognition.start();
      setListening(true);
    } catch {
      setVoiceError('Не удалось запустить микрофон. Введите текст вручную.');
      setListening(false);
      recognitionRef.current = null;
    }
  };

  const submit = async () => {
    const value = prompt.trim();
    if (value.length < MIN_PROMPT) {
      setError(`Опишите сценарий хотя бы в ${MIN_PROMPT} символах.`);
      return;
    }
    // Freeze the voice session before sending: late browser callbacks must not
    // mutate the prompt whose preview is being built.
    stopVoice(true);
    const requestSeq = requestSeqRef.current + 1;
    requestSeqRef.current = requestSeq;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setLoading(true);
    setError(null);
    setPreview(null);
    try {
      const result = await requestScenarioPreview(value, controller.signal);
      if (requestSeq !== requestSeqRef.current || controller.signal.aborted) return;
      setPreview(result);
    } catch (exc) {
      if (controller.signal.aborted || requestSeq !== requestSeqRef.current) return;
      if (exc instanceof ScenarioBuilderApiError) {
        setError(exc.message);
      } else {
        setError('Не удалось получить черновик. Попробуйте позже.');
      }
    } finally {
      if (requestSeq === requestSeqRef.current) {
        setLoading(false);
        abortRef.current = null;
      }
    }
  };

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-3 backdrop-blur-sm sm:p-4">
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="scenario-prompt-title"
        className="flex max-h-[92vh] w-full max-w-3xl flex-col overflow-hidden rounded-2xl border border-slate-800 bg-slate-900 shadow-2xl"
      >
        <div className="flex items-center justify-between border-b border-slate-800 p-4">
          <div className="flex items-center gap-2">
            <Sparkles size={17} className="text-fuchsia-400" />
            <h2 id="scenario-prompt-title" className="text-sm font-bold text-slate-100">
              Собрать сценарий по описанию
            </h2>
          </div>
          <button
            type="button"
            onClick={close}
            className="text-slate-400 hover:text-slate-200"
            aria-label="Закрыть"
          >
            <X size={18} />
          </button>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5">
          {!preview ? (
            <div className="space-y-4">
              <p className="rounded-xl border border-slate-800 bg-slate-950/70 p-3 text-[11px] leading-relaxed text-slate-400">
                Опишите, что проверить. Будут использованы только известные блоки. Сценарий
                не заменит текущий холст, пока вы не нажмёте «Применить».
              </p>

              <label className="block">
                <span className="mb-1 block text-[11px] text-slate-400">Описание сценария</span>
                <textarea
                  value={prompt}
                  onChange={(event) => setPrompt(event.target.value.slice(0, MAX_PROMPT))}
                  maxLength={MAX_PROMPT}
                  rows={7}
                  autoFocus
                  disabled={loading}
                  placeholder="Например: проверь секреты и зависимости, сделай отчёт и отправь уведомление"
                  className="w-full resize-y rounded-xl border border-slate-700 bg-slate-950 px-3 py-2.5 text-sm leading-relaxed text-slate-100 focus:border-fuchsia-500 focus:outline-none disabled:opacity-60"
                />
                <div className="mt-1 flex items-center justify-between text-[10px] text-slate-500">
                  <span>Текст можно отредактировать после распознавания.</span>
                  <span>{prompt.length}/{MAX_PROMPT}</span>
                </div>
              </label>

              <div className="flex flex-wrap items-center gap-2">
                <button
                  type="button"
                  onClick={startVoice}
                  disabled={loading}
                  aria-pressed={listening}
                  className={`inline-flex items-center gap-2 rounded-lg border px-3 py-2 text-xs font-semibold transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    listening
                      ? 'border-rose-700 bg-rose-950/60 text-rose-200'
                      : 'border-slate-700 bg-slate-800 text-slate-200 hover:border-slate-600'
                  }`}
                >
                  {listening ? <Square size={14} /> : <Mic size={14} />}
                  {listening ? 'Остановить запись' : 'Надиктовать'}
                </button>
                {listening && (
                  <span className="inline-flex items-center gap-1.5 text-[11px] text-rose-300" aria-live="polite">
                    <span className="h-2 w-2 animate-pulse rounded-full bg-rose-400" />
                    Слушаю…
                  </span>
                )}
                {!voiceSupported && (
                  <span className="text-[11px] text-slate-500">
                    Голосовой ввод не поддерживается — введите текст вручную.
                  </span>
                )}
              </div>
              <p className="text-[10px] leading-relaxed text-slate-500">
                Распознавание выполняет браузер и может использовать его внешний сервис.
                Аудио на сервер NEDRA не отправляется.
              </p>
              {voiceError && (
                <div role="alert" className="rounded-xl border border-amber-800 bg-amber-950/40 p-3 text-[11px] text-amber-200">
                  {voiceError}
                </div>
              )}

              {error && (
                <div role="alert" className="rounded-xl border border-rose-800 bg-rose-950/70 p-3 text-xs text-rose-200">
                  {error}
                </div>
              )}

              <button
                type="button"
                onClick={submit}
                disabled={loading || prompt.trim().length < MIN_PROMPT}
                className="inline-flex w-full items-center justify-center gap-2 rounded-xl bg-fuchsia-600 px-4 py-2.5 text-xs font-semibold text-white hover:bg-fuchsia-500 disabled:cursor-not-allowed disabled:bg-slate-700 disabled:text-slate-400 sm:w-auto"
              >
                {loading ? <Loader2 size={15} className="animate-spin" /> : <Sparkles size={15} />}
                {loading ? 'Собираем черновик…' : 'Собрать черновик'}
              </button>
            </div>
          ) : (
            <div className="space-y-4">
              <div className="rounded-xl border border-slate-800 bg-slate-950/70 p-4">
                <div className="text-[11px] uppercase tracking-wider text-slate-500">Черновик</div>
                <h3 className="mt-1 text-base font-bold text-slate-100">{preview.draft.name}</h3>
                <p className="mt-2 text-xs leading-relaxed text-slate-300">{preview.summary}</p>
                <p className="mt-2 text-xs text-emerald-300" role="status">
                  Граф проверен: блоки и связи допустимы. Сохранение и запуск — отдельно, в редакторе.
                </p>
              </div>

              {preview.warnings.length > 0 && (
                <div className="rounded-xl border border-amber-800/70 bg-amber-950/30 p-3">
                  <div className="text-[11px] font-semibold text-amber-200">Перед применением</div>
                  <ul className="mt-1 list-disc space-y-1 pl-4 text-[11px] leading-relaxed text-amber-100/90">
                    {preview.warnings.map((warning) => <li key={warning}>{warning}</li>)}
                  </ul>
                </div>
              )}

              <div className="overflow-hidden rounded-xl border border-slate-800">
                <table className="w-full text-left text-[11px]">
                  <thead className="bg-slate-950 text-slate-400">
                    <tr>
                      <th className="px-3 py-2 font-medium">Блок</th>
                      <th className="px-3 py-2 font-medium">Тип</th>
                    </tr>
                  </thead>
                  <tbody className="divide-y divide-slate-800 text-slate-300">
                    {preview.draft.nodes.map((node) => (
                      <tr key={node.id}>
                        <td className="px-3 py-2">{node.title || node.kind}</td>
                        <td className="px-3 py-2 font-mono text-[10px] text-slate-500">{node.kind}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>

              <div className="rounded-xl border border-slate-800 bg-slate-950/50 p-3 text-[11px] text-slate-400">
                <div className="font-semibold text-slate-300">Связи: {preview.draft.edges.length}</div>
                <ul className="mt-2 space-y-1 break-words text-[11px]">
                  {preview.draft.edges.map((edge) => (
                    <li key={edge.id}>
                      {preview.draft.nodes.find((node) => node.id === edge.source)?.title}
                      {' → '}
                      {preview.draft.nodes.find((node) => node.id === edge.target)?.title}
                    </li>
                  ))}
                </ul>
              </div>

              <div className="flex flex-col-reverse gap-2 sm:flex-row sm:justify-end">
                <button
                  type="button"
                  onClick={() => { setPreview(null); setError(null); }}
                  className="rounded-lg border border-slate-700 px-3 py-2 text-xs text-slate-300 hover:bg-slate-800"
                >
                  Изменить описание
                </button>
                <button
                  type="button"
                  onClick={() => { onApply(preview.draft); close(); }}
                  className="rounded-lg bg-emerald-600 px-4 py-2 text-xs font-semibold text-white hover:bg-emerald-500"
                >
                  Применить в редактор
                </button>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
