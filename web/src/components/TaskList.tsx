import type { ReactNode } from 'react';
import type { TaskRecord } from '../types';
import { useNavigation } from '../navigation';
import { TaskRow } from './TaskRow';
export function TaskList({ tasks, actions }: { tasks: TaskRecord[]; actions?: (task: TaskRecord) => ReactNode }) {
  const { data } = useNavigation();
  return <ul className="task-list">{tasks.map(task => {
    const workspace = data?.workspaces.find(w => w.workspace_id === task.workspace_id);
    const repository = data?.repositories.find(r => r.repository_id === workspace?.repository_id);
    const workspaceLabel = workspace?.kind === 'main' ? 'Main' : workspace?.branch || workspace?.path.split('/').pop() || 'Workspace';
    const context = [repository?.name, workspaceLabel].filter(Boolean).join(' · ');
    return <li key={task.task_id}><TaskRow task={task} workspace={context} />{actions?.(task)}</li>;
  })}</ul>;
}
