import { SidebarMenuButton } from './ui/primitives/sidebar';
import { Link } from 'react-router';
import type { TaskRecord } from '../types';
export function taskStatus(task: Pick<TaskRecord, 'status' | 'phase' | 'last_turn'>) {
  if (task.status === 'failed' || (task.status === 'active' && task.last_turn?.ok === false)) return 'Needs attention';
  if (task.status === 'waiting_input') return task.phase === 'Plan approval needed' ? 'Review plan' : 'Waiting for me';
  if (task.status === 'completed') return 'Finished';
  if (task.status === 'active') return task.phase === 'Ready for your next message' ? 'Ready' : 'Working';
  return task.status.replaceAll('_', ' ');
}
export function TaskRow({ task, workspace, selected = false, compact = false }: { task: TaskRecord; workspace?: string; selected?: boolean; compact?: boolean }) {
  const label = taskStatus(task);
  const symbol = task.status === 'waiting_input' ? '?' : label === 'Needs attention' ? '!' : task.status === 'completed' ? '✓' : task.status === 'cancelled' ? '−' : '◌';
  const children = <>
    <span className={`status-symbol ${label === 'Needs attention' ? 'failed' : task.status}`} aria-hidden="true">{symbol}</span><span className="task-row-copy"><span className="task-row-title">{task.title}</span>{compact && task.status === 'waiting_input' && <small>{label}</small>}{!compact && <small>{workspace || task.kind}</small>}</span><span className="task-row-status">{label}{task.archived_at && ' · Archived'}</span>
  </>;
  const className = `task-row ${selected ? 'selected' : ''}`;
  const props = { className, 'aria-current': selected ? 'page' as const : undefined, title: `${task.title} · ${workspace || ''} · ${label}` };
  const link = <Link to={`/tasks/${encodeURIComponent(task.task_id)}`} />;
  return compact ? <SidebarMenuButton render={link} isActive={selected} {...props}>{children}</SidebarMenuButton> : <Link to={`/tasks/${encodeURIComponent(task.task_id)}`} {...props}>{children}</Link>;
}
