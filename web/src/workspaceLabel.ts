import type { WorkspaceRecord } from './types';

export function workspaceLabel(workspace: WorkspaceRecord) {
  if (workspace.kind === 'main') return 'Main checkout';
  const branch = workspace.branch || '';
  const opaque = /^orch\/(?:[a-f0-9]{8}\/[a-f0-9]{8}|task-[a-f0-9-]+)$/.test(branch);
  const name = workspace.path.split('/').filter(Boolean).pop() || 'Worktree';
  return opaque && workspace.originating_task_title
    ? `${workspace.originating_task_title} · ${name.slice(-12)}` : branch || name;
}
