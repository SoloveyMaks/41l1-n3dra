import React, { useId, useState } from 'react';
import {
  Play, Clock, FolderArchive, KeyRound, PackageSearch, FileCode2,
  GitMerge, ShieldAlert, GitFork, Sparkles, FileText, Send,
  ChevronLeft, ChevronRight, Plus
} from 'lucide-react';
import { NodeKind } from '../types';
import './NodePalette.css';

const PALETTE_COLLAPSED_KEY = 'nedra.nodePaletteCollapsed';

const readPaletteCollapsed = (): boolean => {
  try {
    return window.localStorage.getItem(PALETTE_COLLAPSED_KEY) === '1';
  } catch {
    return false;
  }
};

interface NodePaletteProps {
  onAddNode: (kind: NodeKind) => void;
  onCreateCustomBlock?: () => void;
  onNodeDragStart?: (event: React.DragEvent<HTMLButtonElement>, kind: NodeKind) => void;
}

type IconComponent = React.ComponentType<{ size?: number; className?: string }>;

const PALETTE_ITEMS: Array<{
  category: string;
  items: Array<{ kind: NodeKind; title: string; desc: string; icon: IconComponent; color: string }>;
}> = [
  {
    category: 'Запуск',
    items: [
      { kind: 'manual_trigger', title: 'Ручной запуск', desc: 'Запуск по клику', icon: Play, color: 'text-purple-400 border-purple-800/60 bg-purple-950/30' },
      { kind: 'schedule_trigger', title: 'По расписанию', desc: 'Запуск по расписанию', icon: Clock, color: 'text-purple-400 border-purple-800/60 bg-purple-950/30' },
    ],
  },
  {
    category: 'Исходный проект',
    items: [
      { kind: 'project_input', title: 'Проект для проверки', desc: 'ZIP-архив или репозиторий', icon: FolderArchive, color: 'text-blue-400 border-blue-800/60 bg-blue-950/30' },
    ],
  },
  {
    category: 'Проверки кода',
    items: [
      { kind: 'secret_scan', title: 'Поиск секретов', desc: 'Поиск паролей и токенов', icon: KeyRound, color: 'text-amber-400 border-amber-800/60 bg-amber-950/30' },
      { kind: 'sca_scan', title: 'Зависимости', desc: 'Известные уязвимости Gradle/KTS', icon: PackageSearch, color: 'text-orange-400 border-orange-800/60 bg-orange-950/30' },
      { kind: 'kotlin_sast', title: 'Анализ Kotlin', desc: 'Пути небезопасных данных', icon: FileCode2, color: 'text-rose-400 border-rose-800/60 bg-rose-950/30' },
    ],
  },
  {
    category: 'Результаты и правила',
    items: [
      { kind: 'merge_findings', title: 'Объединить находки', desc: 'Слияние результатов', icon: GitMerge, color: 'text-teal-400 border-teal-800/60 bg-teal-950/30' },
      { kind: 'policy_gate', title: 'Проверить правила', desc: 'Итог по правилам безопасности', icon: ShieldAlert, color: 'text-emerald-400 border-emerald-800/60 bg-emerald-950/30' },
    ],
  },
  {
    category: 'Ветвление сценария',
    items: [
      { kind: 'condition', title: 'Условие', desc: 'Развилка по условию или шаблону', icon: GitFork, color: 'text-yellow-400 border-yellow-800/60 bg-yellow-950/30' },
      { kind: 'branch_merge', title: 'Объединить ветви', desc: 'Продолжить после развилки', icon: GitMerge, color: 'text-indigo-400 border-indigo-800/60 bg-indigo-950/30' },
    ],
  },
  {
    category: 'AI-анализ кода',
    items: [
      { kind: 'ai_scan', title: 'AI: поиск уязвимостей', desc: 'Гипотезы по коду со скрытыми секретами', icon: Sparkles, color: 'text-fuchsia-400 border-fuchsia-800/60 bg-fuchsia-950/30' },
    ],
  },
  {
    category: 'Объяснения и отчёты',
    items: [
      { kind: 'ai_explain', title: 'Объяснить находки', desc: 'Причины и рекомендации по исправлению', icon: Sparkles, color: 'text-fuchsia-400 border-fuchsia-800/60 bg-fuchsia-950/30' },
      { kind: 'build_report', title: 'Собрать отчёт', desc: 'Паспорт безопасности и JSON', icon: FileText, color: 'text-cyan-400 border-cyan-800/60 bg-cyan-950/30' },
      { kind: 'notify', title: 'Отправить уведомление', desc: 'Оповещение через вебхук', icon: Send, color: 'text-sky-400 border-sky-800/60 bg-sky-950/30' },
    ],
  },
];

export const NodePalette: React.FC<NodePaletteProps> = ({
  onAddNode,
  onCreateCustomBlock,
  onNodeDragStart,
}) => {
  const [collapsed, setCollapsed] = useState(readPaletteCollapsed);
  const listId = useId();
  const headingId = useId();

  const toggleCollapsed = () => {
    const next = !collapsed;
    setCollapsed(next);
    try {
      window.localStorage.setItem(PALETTE_COLLAPSED_KEY, next ? '1' : '0');
    } catch {
      // Storage can be blocked; the palette remains usable in this session.
    }
  };

  const toggleLabel = collapsed ? 'Развернуть палитру блоков' : 'Свернуть палитру блоков';

  return (
    <aside
      aria-labelledby={headingId}
      className={`node-palette${collapsed ? ' node-palette--collapsed' : ''}`}
    >
      <div className="node-palette__header">
        <div className="node-palette__intro">
          <h2 id={headingId}>Палитра блоков</h2>
          {!collapsed && <p>Нажмите блок, чтобы добавить в сценарий</p>}
        </div>
        <button
          type="button"
          onClick={toggleCollapsed}
          aria-label={toggleLabel}
          aria-expanded={!collapsed}
          aria-controls={listId}
          title={toggleLabel}
          className="node-palette__toggle"
        >
          {collapsed ? <ChevronRight size={18} aria-hidden="true" /> : <ChevronLeft size={18} aria-hidden="true" />}
        </button>
      </div>

      <div id={listId} hidden={collapsed} className="node-palette__list">
        {PALETTE_ITEMS.map((cat) => (
          <section key={cat.category} className="node-palette__category">
            <h3>{cat.category}</h3>
            <div className="node-palette__items">
              {cat.items.map((item) => {
                const Icon = item.icon;
                return (
                  <button
                    type="button"
                    key={item.kind}
                    onClick={() => onAddNode(item.kind)}
                    draggable={Boolean(onNodeDragStart)}
                    onDragStart={onNodeDragStart ? (event) => onNodeDragStart(event, item.kind) : undefined}
                    className={`node-palette__item ${item.color}`}
                  >
                    <Icon size={16} className="node-palette__item-icon" aria-hidden="true" />
                    <span className="node-palette__item-copy">
                      <span className="node-palette__item-title">{item.title}</span>
                      <span className="node-palette__item-description">{item.desc}</span>
                    </span>
                  </button>
                );
              })}
            </div>
          </section>
        ))}
        {onCreateCustomBlock && (
          <button type="button" className="node-palette__custom" onClick={onCreateCustomBlock}>
            <Plus size={16} aria-hidden="true" />
            Создать свой блок
          </button>
        )}
      </div>
      {collapsed && <span className="node-palette__rail-label" aria-hidden="true">Блоки</span>}
    </aside>
  );
};
