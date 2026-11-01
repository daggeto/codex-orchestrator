import type { TaskRecord, WorkspaceRecord } from './types';

export function workspaceLabel(workspace: WorkspaceRecord, tasks: TaskRecord[] = []) {
  if (workspace.kind === 'main') return 'Main checkout';
  const branch = workspace.branch || '';
  const opaque = /^orch\/(?:[a-f0-9]{8}\/[a-f0-9]{8}|task-[a-f0-9-]+)$/.test(branch);
  const task = tasks.filter(item => item.workspace_id === workspace.workspace_id)
    .sort((a, b) => a.task_id.localeCompare(b.task_id))[0];
  const name = workspace.path.split('/').filter(Boolean).pop() || 'Worktree';
  return opaque && task ? `${task.title} · ${name.slice(-12)}` : branch || name;
}
