import type { NodeDefinition, NodeKind, WorkflowDraft, WorkflowEdge, WorkflowNode } from '../types';

export function addCheck(
  draft: WorkflowDraft,
  kind: NodeKind,
  definition?: NodeDefinition,
  custom?: { id: string; name: string; version: string },
): { draft: WorkflowDraft; node: WorkflowNode } {
  const id = `node_${kind}_${crypto.randomUUID()}`;
  const project = draft.nodes.find((node) => node.kind === 'project_input');
  const scanners = draft.nodes.filter((node) => ['secret_scan', 'sca_scan', 'kotlin_sast', 'ai_scan', 'custom_check'].includes(node.kind));
  const node: WorkflowNode = {
    id, kind, version: 1,
    title: custom ? `${custom.name} · v${custom.version}` : definition?.title || kind,
    config: { ...definition?.default_config, ...(custom ? { definitionId: custom.id } : {}) },
    position: { x: project ? project.position.x + 260 : 500, y: 100 + scanners.length * 160 },
  };
  const edges: WorkflowEdge[] = [];
  const connect = (source: string, sourcePort: string, target: string, targetPort: string) => {
    edges.push({ id: `e_${crypto.randomUUID()}`, source, sourcePort, target, targetPort });
  };
  if (project && ['ai_scan', 'custom_check'].includes(kind)) {
    connect(project.id, 'artifact', id, 'artifact');
  }
  if (kind === 'ai_scan') {
    const merge = draft.nodes.find((item) => item.kind === 'merge_findings');
    if (merge) connect(merge.id, 'findings_bundle', id, 'findings_bundle');
    for (const report of draft.nodes.filter((item) => item.kind === 'build_report')) {
      if (!draft.edges.some((edge) => edge.target === report.id && edge.targetPort === 'ai_scan_result')) {
        connect(id, 'ai_scan_result', report.id, 'ai_scan_result');
      }
    }
  }
  return { draft: { ...draft, nodes: [...draft.nodes, node], edges: [...draft.edges, ...edges] }, node };
}
