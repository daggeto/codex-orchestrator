import type { ReactNode } from 'react';
import type { TaskRecord } from '../types';
import { useNavigation } from '../navigation';
import { TaskRow } from './TaskRow';
export function TaskList({ tasks, actions }: { tasks: TaskRecord[]; actions?: (task: TaskRecord) => ReactNode }) {
  const { data } = useNavigation();
  return <ul className="task-list">{tasks.map(task => { const workspace = data?.workspaces.find(w => w.workspace_id === task.workspace_id); return <li key={task.task_id}><TaskRow task={task} workspace={workspace?.kind === 'main' ? 'Main' : workspace?.branch || workspace?.path.split('/').pop() || 'Workspace'} />{actions?.(task)}</li>; })}</ul>;
}
