import { FileCode2 } from 'lucide-react';
import type { ComponentType } from 'react';
import type { NodeKind } from '../types';

export interface CustomCheckPaletteItem {
  kind: NodeKind;
  title: string;
  desc: string;
  icon: ComponentType<{ size?: number; className?: string }>;
  color: string;
}

export const CUSTOM_CHECK_PALETTE_ITEM: CustomCheckPaletteItem = {
  kind: 'custom_check',
  title: 'Свой блок проверки',
  desc: 'Добавить сохранённую Python-проверку',
  icon: FileCode2,
  color: 'text-violet-300 border-violet-800/60 bg-violet-950/30',
};