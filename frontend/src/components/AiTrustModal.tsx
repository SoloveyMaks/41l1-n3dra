import React, { useEffect, useRef, useState } from 'react';
import { Sparkles, X, ShieldAlert, Loader2 } from 'lucide-react';
import { apiClient } from '../api/client';

interface AiTrustModalProps {
  runId?: string;
  isOpen: boolean;
  onClose: () => void;
}

interface AiTrustResult {
  is_valid: boolean;
  errors: string[];
  synthetic_response: unknown;
}

const parseTrustResult = (value: unknown): AiTrustResult | null => {
  if (!value || typeof value !== 'object') return null;
  const record = value as Record<string, unknown>;
  if (typeof record.is_valid !== 'boolean') return null;
  return {
    is_valid: record.is_valid,
    errors: Array.isArray(record.errors) ? record.errors.filter((item): item is string => typeof item === 'string') : [],
    synthetic_response: record.synthetic_response ?? null,
  };
};

export const AiTrustModal: React.FC<AiTrustModalProps> = ({ runId, isOpen, onClose }) => {
  const [isRunning, setIsRunning] = useState(false);
  const [result, setResult] = useState<AiTrustResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const requestSeq = useRef(0);

  useEffect(() => {
    requestSeq.current += 1;
    setIsRunning(false);
    setResult(null);
    setError(null);
  }, [isOpen, runId]);

  if (!isOpen) return null;

  const handleRunTest = async () => {
    const seq = ++requestSeq.current;
    setIsRunning(true);
    setResult(null);
    setError(null);
    try {
      const parsed = parseTrustResult(await apiClient.runAiSyntheticTest(runId));
      if (seq !== requestSeq.current) return;
      if (!parsed) {
        setError('Сервер вернул неполный ответ теста');
        return;
      }
      setResult(parsed);
    } catch (error: unknown) {
      if (seq !== requestSeq.current) return;
      setError(error instanceof Error ? error.message : 'Синтетический тест недоступен');
    } finally {
      if (seq === requestSeq.current) setIsRunning(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-2xl overflow-hidden shadow-2xl">
        {/* Header */}
        <div className="p-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles size={18} className="text-fuchsia-400" />
            <h3 className="text-sm font-bold text-slate-100">
              Демо для жюри: Проверка защитного слоя AI Trust Layer
            </h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        {/* Body */}
        <div className="p-6 space-y-4 text-xs text-slate-300">
          <p className="leading-relaxed text-slate-400">
            Здесь демонстрируется программный защитный слой (AI Trust Layer). Мы намеренно скармливаем валидатору 
            синтетический ответ LLM с вымышленным идентификатором уязвимости (<code className="text-rose-400">CVE-2099-99999</code>) 
            и несуществующим <code className="text-rose-400">findingId</code>.
          </p>

          <button
            onClick={handleRunTest}
            disabled={isRunning}
            className="w-full py-2.5 px-4 rounded-xl bg-fuchsia-600 hover:bg-fuchsia-500 text-white font-bold transition flex items-center justify-center gap-2 cursor-pointer shadow-lg shadow-fuchsia-600/20"
          >
            {isRunning ? <Loader2 size={16} className="animate-spin" /> : <ShieldAlert size={16} />}
            <span>Запустить стресс-тест валидатора</span>
          </button>

          {error && (
            <div role="alert" className="rounded-xl border border-rose-800 bg-rose-950/60 p-3 text-xs text-rose-200">
              Тест AI Trust Layer не выполнен: {error}
            </div>
          )}

          {result && (
            <div className="space-y-4 pt-2">
              {/* Verdict */}
              <div
                className={`p-3.5 rounded-xl border flex items-center justify-between ${
                  result.is_valid
                    ? 'bg-emerald-950/60 border-emerald-800 text-emerald-200'
                    : 'bg-rose-950/80 border-rose-800 text-rose-200'
                }`}
              >
                <div className="flex items-center gap-2">
                  <ShieldAlert size={18} className="text-rose-400" />
                  <span className="font-bold text-sm">
                    {result.is_valid ? 'ВАЛИДАТОР ПРОПУСТИЛ' : 'ВАЛИДАТОР УСПЕШНО ЗАБЛОКИРОВАЛ ГАЛЛЮЦИНАЦИЮ'}
                  </span>
                </div>
                <span className="text-[10px] uppercase font-mono px-2 py-0.5 rounded bg-black/40 border border-rose-700">
                  {(result.errors || []).length} нарушений зафиксировано
                </span>
              </div>

              {/* Errors Caught */}
              <div>
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1.5">
                  Ошибки, заблокированные валидатором:
                </div>
                <div className="space-y-1.5">
                  {(result.errors || []).map((err: string, idx: number) => (
                    <div
                      key={idx}
                      className="p-2.5 rounded-lg bg-rose-950/40 border border-rose-800/60 text-[11px] font-mono text-rose-300 flex items-start gap-2"
                    >
                      <span className="text-rose-400 font-bold shrink-0">✕</span>
                      <span>{err}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Synthetic Payload Preview */}
              <div>
                <div className="text-[10px] font-bold text-slate-400 uppercase tracking-wider mb-1">
                  Синтетический вредоносный ответ модели:
                </div>
                <pre className="p-3 rounded-xl bg-slate-950 border border-slate-800 font-mono text-[10px] text-slate-400 max-h-40 overflow-y-auto">
                  {JSON.stringify(result.synthetic_response, null, 2)}
                </pre>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
};
