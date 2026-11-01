import { Link } from 'react-router';
import type { TaskRecord } from '../types';
export const taskStatus = (status: string, phase = '') => status === 'active' && phase === 'Ready for your next message' ? 'Ready' : status === 'active' && phase.includes('retry') ? 'Needs attention' : status === 'waiting_input' ? 'Waiting for me' : status === 'completed' ? 'Finished' : status === 'active' ? 'Working' : status.replaceAll('_', ' ');
export function TaskRow({ task, workspace, selected = false }: { task: TaskRecord; workspace?: string; selected?: boolean }) {
  const symbol = task.status === 'waiting_input' ? '?' : task.status === 'failed' ? '!' : task.status === 'completed' ? '✓' : task.status === 'cancelled' ? '−' : '◌';
  return <Link className={`task-row ${selected ? 'selected' : ''}`} aria-current={selected ? 'page' : undefined} to={`/tasks/${encodeURIComponent(task.task_id)}`} title={`${task.title} · ${workspace || ''} · ${taskStatus(task.status, task.phase)}`}>
    <span className={`status-symbol ${task.status}`} aria-hidden="true">{symbol}</span><span className="task-row-copy"><span className="task-row-title">{task.title}</span><small>{workspace || task.kind}</small></span><span className="task-row-status">{taskStatus(task.status, task.phase)}{task.archived_at && ' · Archived'}</span>
  </Link>;
}
