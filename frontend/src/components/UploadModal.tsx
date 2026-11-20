import React, { useState } from 'react';
import { Upload, X, FileArchive, CheckCircle2, AlertCircle, Loader2, Sparkles } from 'lucide-react';
import { ArtifactManifest } from '../types';
import { apiClient } from '../api/client';

interface UploadModalProps {
  isOpen: boolean;
  onClose: () => void;
  onArtifactLoaded: (manifest: ArtifactManifest) => void;
}

export const UploadModal: React.FC<UploadModalProps> = ({ isOpen, onClose, onArtifactLoaded }) => {
  const [isUploading, setIsUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!isOpen) return null;

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    setError(null);
    try {
      const manifest = await apiClient.uploadArtifact(file);
      onArtifactLoaded(manifest);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки архива');
    } finally {
      setIsUploading(false);
    }
  };

  const handleLoadSample = async () => {
    setIsUploading(true);
    setError(null);
    try {
      const manifest = await apiClient.loadSampleArtifact();
      onArtifactLoaded(manifest);
      onClose();
    } catch (err: any) {
      setError(err.message || 'Ошибка загрузки учебного архива');
    } finally {
      setIsUploading(false);
    }
  };

  return (
    <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4">
      <div className="bg-slate-900 border border-slate-800 rounded-2xl w-full max-w-md overflow-hidden shadow-2xl">
        <div className="p-4 border-b border-slate-800 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Upload size={18} className="text-indigo-400" />
            <h3 className="text-sm font-bold text-slate-100">Загрузка учебного проекта (ZIP)</h3>
          </div>
          <button onClick={onClose} className="text-slate-400 hover:text-slate-200">
            <X size={16} />
          </button>
        </div>

        <div className="p-6 space-y-4">
          {error && (
            <div className="p-3 rounded-xl bg-rose-950/80 border border-rose-800 text-xs text-rose-200 flex items-start gap-2">
              <AlertCircle size={16} className="shrink-0 mt-0.5 text-rose-400" />
              <span>{error}</span>
            </div>
          )}

          {/* Drag & drop / upload zone */}
          <label className="border-2 border-dashed border-slate-700 hover:border-indigo-500 rounded-xl p-8 flex flex-col items-center justify-center text-center cursor-pointer transition bg-slate-950/40 hover:bg-slate-950/60">
            <FileArchive size={36} className="text-slate-500 mb-2" />
            <span className="text-xs font-semibold text-slate-200">
              Выберите ZIP-архив с проектом
            </span>
            <span className="text-[10px] text-slate-400 mt-1">
              Проверка: лимит 50MB, защита от path traversal и zip-бомб
            </span>
            <input
              type="file"
              accept=".zip"
              onChange={handleFileUpload}
              disabled={isUploading}
              className="hidden"
            />
          </label>

          <div className="relative flex py-1 items-center">
            <div className="flex-grow border-t border-slate-800"></div>
            <span className="flex-shrink mx-3 text-[10px] text-slate-500 uppercase tracking-wider">
              или используйте готовый эталон
            </span>
            <div className="flex-grow border-t border-slate-800"></div>
          </div>

          <button
            onClick={handleLoadSample}
            disabled={isUploading}
            className="w-full py-2.5 px-4 rounded-xl bg-indigo-950/60 hover:bg-indigo-900/80 border border-indigo-700 text-indigo-300 text-xs font-semibold flex items-center justify-center gap-2 transition cursor-pointer shadow-md"
          >
            {isUploading ? (
              <Loader2 size={16} className="animate-spin text-indigo-400" />
            ) : (
              <Sparkles size={16} className="text-indigo-400" />
            )}
            <span>Загрузить эталонный проект хакатона (Kotlin/Spring Boot)</span>
          </button>
        </div>
      </div>
    </div>
  );
};
