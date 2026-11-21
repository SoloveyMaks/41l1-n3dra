import { ApiError } from './api/client';
import { ArtifactManifest, RepositoryProvider, RepositorySource } from './types';

export interface ParsedRepository {
  url: string;
  provider: RepositoryProvider;
}

const PROVIDERS: Record<string, RepositoryProvider> = {
  'github.com': 'github',
  'www.github.com': 'github',
  'gitlab.com': 'gitlab',
  'www.gitlab.com': 'gitlab',
};

const REPO_HOSTS = new Set(Object.keys(PROVIDERS));

/**
 * Принимаем только публичный HTTPS-репозиторий на github.com/gitlab.com.
 * Возвращаем canonical URL без .git, query, fragment и без учётных данных.
 */
export const parseRepositoryUrl = (value: string): ParsedRepository => {
  const raw = value.trim();
  if (!raw) throw new Error('Вставьте ссылку на публичный репозиторий GitHub или GitLab');
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error('Это не похоже на ссылку. Пример: https://github.com/owner/repo');
  }
  if (parsed.protocol !== 'https:') {
    throw new Error('Нужна HTTPS-ссылка: http и git@ не поддерживаются');
  }
  if (parsed.username || parsed.password) {
    throw new Error('Уберите логин и токен из ссылки: приватный доступ пока не поддерживается');
  }
  if (!REPO_HOSTS.has(parsed.hostname.toLowerCase())) {
    throw new Error('Поддерживаются только github.com и gitlab.com');
  }
  if (parsed.search || parsed.hash) {
    throw new Error('Уберите параметры из ссылки: нужна ссылка на репозиторий');
  }
  const provider = PROVIDERS[parsed.hostname.toLowerCase()];
  const segments = parsed.pathname.split('/').filter(Boolean);
  if (segments.length < 2) {
    throw new Error('Нужна ссылка на репозиторий: https://github.com/owner/repo');
  }
  const last = segments[segments.length - 1];
  if (last.endsWith('.git')) segments[segments.length - 1] = last.slice(0, -4);
  if (provider === 'github' && segments.length !== 2) {
    throw new Error('Для GitHub нужна ссылка на корень репозитория: https://github.com/owner/repo');
  }
  const clean = segments.map((part) => part.trim()).filter(Boolean);
  if (clean.some((part) => part === '.' || part === '..')) {
    throw new Error('Некорректный адрес репозитория');
  }
  return { url: `https://${parsed.hostname.toLowerCase().replace(/^www\./, '')}/${clean.join('/')}`, provider };
};

export const validateBranch = (value: string): string => {
  const branch = value.trim();
  if (!branch) return '';
  if (branch.length > 255) throw new Error('Название ветки слишком длинное');
  if (!/^[A-Za-z0-9._/-]+$/.test(branch) || branch.includes('..') || branch.startsWith('/') || branch.endsWith('/') || branch.includes('//')) {
    throw new Error('Некорректное название ветки. Пример: main или feature/login');
  }
  return branch;
};

export const isValidCronOrInterval = (value: string): boolean => {
  const spec = value.trim();
  if (/^interval:\d+$/.test(spec)) return Number(spec.split(':')[1]) >= 60;
  const parts = spec.split(/\s+/);
  return parts.length === 5 && parts.every((part) => /^[0-9A-Za-z*/,?\-/]+$/.test(part));
};

export const shortCommit = (sha?: string | null): string => (sha ? sha.slice(0, 7) : '—');

export const providerLabel = (provider?: RepositoryProvider | null): string =>
  provider === 'gitlab' ? 'GitLab' : provider === 'github' ? 'GitHub' : 'Репозиторий';

export const repositoryShortName = (url: string): string => {
  const path = url.replace(/^https:\/\/[^/]+\/?/, '').replace(/\/+$/, '');
  return path || url;
};

export const artifactRepository = (artifact?: ArtifactManifest | null): RepositorySource | null =>
  artifact?.repository || null;

export const artifactLabel = (artifact?: ArtifactManifest | null): string => {
  if (!artifact) return 'Проект не выбран';
  return artifact.repository ? repositoryShortName(artifact.repository.url) : artifact.filename;
};

export const artifactChoiceLabel = (artifact: ArtifactManifest): string =>
  artifact.repository
    ? `${artifactLabel(artifact)} · ${shortCommit(artifact.repository.commit_sha)}`
    : artifact.filename;

export const artifactKey = (artifact: ArtifactManifest): string =>
  artifact.repository
    ? `${artifact.repository.url}@${artifact.repository.commit_sha}`
    : `${artifact.filename}#${artifact.id}`;

export const findArtifact = (artifactId: string | undefined | null, artifacts: ArtifactManifest[]): ArtifactManifest | undefined =>
  artifactId ? artifacts.find((artifact) => artifact.id === artifactId) : undefined;

/** Человеческое объяснение ошибки бэкенда, без HTML и сырых ссылок. */
export const describeRepositoryError = (error: unknown): string => {
  const message = error instanceof Error ? error.message : '';
  const status = error instanceof ApiError ? error.status : 0;
  if (message && !/^HTTP \d+$/.test(message) && message !== 'Not Found' && message !== 'Bad Gateway') return message;
  if (status === 400) return 'Ссылка или ветка не подходят. Проверьте адрес репозитория.';
  if (status === 401) return 'Сессия истекла. Войдите заново.';
  if (status === 403) return 'Недостаточно прав: управление расписаниями доступно администратору.';
  if (status === 404) return 'Импорт из репозитория пока недоступен на этом сервере либо репозиторий не найден. Приватные репозитории не поддерживаются.';
  if (status === 413) return 'Архив репозитория слишком большой для проверки.';
  if (status === 429) return 'GitHub или GitLab временно ограничили запросы. Повторите через минуту.';
  if (status === 502) return 'Не удалось скачать репозиторий: провайдер не ответил. Повторите позже.';
  if (status === 503) return 'Сервис временно недоступен. Повторите позже.';
  return message || 'Не удалось выполнить запрос';
};