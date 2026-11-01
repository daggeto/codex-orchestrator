import { Link } from 'react-router';
import { useNavigation } from '../navigation';
import type { OwnedAgent, TaskRecord } from '../types';
import { Badge } from './ui/Badge';
import { taskStatus } from './TaskRow';
export function TaskContext({ task, title }: { task: TaskRecord; title?: string }) {
  const { data } = useNavigation();
  const workspace = data?.workspaces.find(w => w.workspace_id === task.workspace_id);
  const repository = data?.repositories.find(r => r.repository_id === workspace?.repository_id);
  return <header className="task-context"><div className="context-breadcrumb">{repository && <><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link><span>/</span></>}<Link to={`/workspaces/${task.workspace_id}`}>{workspace?.kind === 'main' ? 'Main' : workspace?.branch || 'Workspace'}</Link>{title && <><span>/</span><Link to={`/tasks/${encodeURIComponent(task.task_id)}`}>{task.title}</Link></>}</div><h1>{title || task.title}</h1><div className="task-state" role="status" aria-live="polite" aria-atomic="true"><Badge variant={task.status === 'waiting_input' ? 'warning' : task.status === 'failed' ? 'danger' : task.status === 'completed' ? 'success' : 'neutral'}>{taskStatus(task.status, task.phase)}</Badge><span>{task.phase}</span>{task.archived_at && <Badge>Archived</Badge>}</div></header>;
}
export function ParticipantStrip({ agents, taskId, selected, onSelect }: { agents: OwnedAgent[]; taskId: string; selected?: string; onSelect?: (id: string) => void }) {
  return <nav className="participant-strip" aria-label="Task participants">{agents.map(agent => <div className="participant" key={agent.agent_id}>{onSelect ? <button aria-pressed={selected === agent.agent_id} onClick={() => onSelect(agent.agent_id)}><span aria-hidden="true">◉</span> {agent.role}<small>{agent.activity?.state === 'working' ? 'Working' : agent.status}</small></button> : <Link aria-current={selected === agent.agent_id ? 'page' : undefined} to={`/agents/${agent.agent_id}?task_id=${encodeURIComponent(taskId)}`}>{agent.role}</Link>}{onSelect && <Link className="participant-detail" aria-label={`Open ${agent.role} agent details`} to={`/agents/${agent.agent_id}?task_id=${encodeURIComponent(taskId)}`}>↗</Link>}</div>)}</nav>;
}
