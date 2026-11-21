import type { ReactNode } from "react";

export type NodeTone =
  | "actor"
  | "route"
  | "access"
  | "handler"
  | "source"
  | "operation"
  | "data"
  | "config"
  | "unknown";

export type NodeMeta = {
  label: string;
  tone: NodeTone;
  icon: ReactNode;
};

const icons: Record<string, ReactNode> = {
  actor: (
    <>
      <circle cx="12" cy="8" r="3.5" />
      <path d="M5 20c.7-4 3.2-6 7-6s6.3 2 7 6" />
    </>
  ),
  route: (
    <>
      <path d="M4 7h9a4 4 0 0 1 0 8H8" />
      <path d="m11 12-3 3 3 3" />
      <path d="M4 17h2" />
    </>
  ),
  access: (
    <>
      <path d="M12 3 4 6v5c0 5 3.5 8 8 10 4.5-2 8-5 8-10V6l-8-3Z" />
      <path d="m9 12 2 2 4-5" />
    </>
  ),
  handler: (
    <>
      <path d="m8 8-4 4 4 4M16 8l4 4-4 4M14 5l-4 14" />
    </>
  ),
  source: (
    <>
      <path d="M6 3h8l4 4v14H6z" />
      <path d="M14 3v5h4M9 13h6M9 17h4" />
    </>
  ),
  operation: (
    <>
      <ellipse cx="12" cy="6" rx="7" ry="3" />
      <path d="M5 6v12c0 1.7 3.1 3 7 3s7-1.3 7-3V6" />
      <path d="M5 12c0 1.7 3.1 3 7 3s7-1.3 7-3" />
    </>
  ),
  data: (
    <>
      <path d="M4 7c0-2 3.6-3.5 8-3.5S20 5 20 7s-3.6 3.5-8 3.5S4 9 4 7Z" />
      <path d="M4 7v10c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5V7" />
      <path d="M4 12c0 2 3.6 3.5 8 3.5s8-1.5 8-3.5" />
    </>
  ),
  config: (
    <>
      <circle cx="12" cy="12" r="3" />
      <path d="M12 2v3M12 19v3M4.2 4.2l2.1 2.1M17.7 17.7l2.1 2.1M2 12h3M19 12h3M4.2 19.8l2.1-2.1M17.7 6.3l2.1-2.1" />
    </>
  ),
  unknown: (
    <>
      <circle cx="12" cy="12" r="8" />
      <path d="M9.8 9.5a2.4 2.4 0 1 1 3.6 2.1c-.9.5-1.4 1.1-1.4 2.2M12 17.2h.01" />
    </>
  ),
};

const META: Record<string, NodeMeta> = {
  actor: { label: "Актор", tone: "actor", icon: icons.actor },
  route: { label: "Маршрут", tone: "route", icon: icons.route },
  security_chain: { label: "Цепочка", tone: "access", icon: icons.access },
  matcher: { label: "Matcher", tone: "access", icon: icons.access },
  guard: { label: "Guard", tone: "access", icon: icons.access },
  function: { label: "Обработчик", tone: "handler", icon: icons.handler },
  source: { label: "Источник", tone: "source", icon: icons.source },
  sink: { label: "Операция", tone: "operation", icon: icons.operation },
  data: { label: "Данные", tone: "data", icon: icons.data },
  config: { label: "Конфиг", tone: "config", icon: icons.config },
};

export const KIND_FALLBACK: NodeMeta = {
  label: "Факт",
  tone: "unknown",
  icon: icons.unknown,
};

export const nodeMeta = (kind: string): NodeMeta => META[kind] ?? KIND_FALLBACK;

export function NodeIcon({ kind, size = 18 }: { kind: string; size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.8"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {nodeMeta(kind).icon}
    </svg>
  );
}

export const toneColor: Record<NodeTone, string> = {
  actor: "#a78bfa",
  route: "#38bdf8",
  access: "#34d399",
  handler: "#60a5fa",
  source: "#c084fc",
  operation: "#fb7185",
  data: "#fbbf24",
  config: "#94a3b8",
  unknown: "#f59e0b",
};