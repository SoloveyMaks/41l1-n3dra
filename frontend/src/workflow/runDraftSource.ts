import type { WorkflowDraft } from '../types';

// Draft, загруженный вызывающим для известного workflow id
// (handleSelectWorkflow тянет его из getWorkflow перед сменой сценария).
export type LoadedDraft = { workflowId: string; draft: WorkflowDraft };

export type RunDraftSource =
  | { mode: 'fetch' }
  | { mode: 'local'; draft: WorkflowDraft };

// Единственная точка решения, откуда alignWorkflow берёт draft для запуска.
// Инвариант: локальный draft применяется, только если он доказуменно
// принадлежит runWorkflowId — override загружен ровно для этого workflow,
// либо override нет вообще и caller гарантирует, что замыкание draft
// принадлежит alignedWorkflowId (выбор запуска внутри открытого сценария).
// Любое несовпадение уходит на fetch с сервера: запуск никогда не может
// установить граф чужого workflow.
export function resolveRunDraftSource(
  runWorkflowId: string,
  alignedWorkflowId: string,
  override: LoadedDraft | undefined,
  closureDraft: WorkflowDraft,
): RunDraftSource {
  if (runWorkflowId !== alignedWorkflowId) return { mode: 'fetch' };
  if (override) {
    return override.workflowId === runWorkflowId
      ? { mode: 'local', draft: override.draft }
      : { mode: 'fetch' };
  }
  return { mode: 'local', draft: closureDraft };
}
