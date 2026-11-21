import { FileCode2 } from 'lucide-react';
import type { ComponentType } from 'react';
import type { NodeKind } from '../types';

/**
 * Готовый элемент палитры для custom-ноды.
 *
 * `NodePalette.tsx` принадлежит отдельному worker-у, поэтому файл палитры здесь
 * не правится. Root вставляет элемент в категорию «3. Собственные анализаторы»:
 *
 * ```tsx
 * import { CUSTOM_CHECK_PALETTE_ITEM } from './customCheckPaletteItem';
 *
 * items: [
 *   // ... secret_scan, sca_scan, kotlin_sast
 *   CUSTOM_CHECK_PALETTE_ITEM,
 * ],
 * ```
 */
export interface CustomCheckPaletteItem {
  kind: NodeKind;
  title: string;
  desc: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  color: string;
}

export const CUSTOM_CHECK_PALETTE_ITEM: CustomCheckPaletteItem = {
  kind: 'custom_check',
  title: 'Custom Check',
  desc: 'Python-проверка в bwrap',
  icon: FileCode2,
  color: 'text-violet-300 border-violet-800/60 bg-violet-950/30',
};