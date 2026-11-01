import { Link, useLocation } from 'react-router';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import type { TaskView } from '../types';
import { TaskRow } from './TaskRow';
import { workspaceLabel } from '../workspaceLabel';
export function RepositorySidebar() {
  const { data, error, expanded, toggle } = useNavigation();
  const { pathname, search } = useLocation();
  const contextId = pathname.startsWith('/tasks/') ? decodeURIComponent(pathname.slice(7)) : pathname.startsWith('/agents/') ? new URLSearchParams(search).get('task_id') : null;
  // Fetch detail independently of the capped preview, including on contextual agent routes.
  const selected = useResource<TaskView>(contextId ? `/api/tasks/${encodeURIComponent(contextId)}` : null, contextId ? 3000 : 0);
  const selectedId = pathname.startsWith('/agents/') && !selected.data?.agents.some(a => pathname === `/agents/${a.agent_id}`) ? null : contextId;
  const tasks = [...(data?.tasks || [])];
  if (selectedId && selected.data?.task.task_id === selectedId && !tasks.some(t => t.task_id === selectedId)) tasks.unshift(selected.data.task);
  return <aside className="repository-sidebar"><Link className="brand" to="/"><span className="brand-mark" aria-hidden="true">O</span>Orchestrator</Link><nav aria-label="Task navigation">
    <div className="task-tabs"><Link to="/tasks" aria-current={pathname === '/tasks' && !search.includes('archive') ? 'page' : undefined}>Tasks</Link><Link to="/tasks?tab=archive" aria-current={search.includes('tab=archive') ? 'page' : undefined}>Archive</Link></div>
    {!data && !error && <p role="status">Loading repositories…</p>}
    {data?.repositories.length === 0 && <p>No repositories yet.</p>}
    {data?.repositories.map(repository => {
      const workspaces = data.workspaces.filter(w => w.repository_id === repository.repository_id);
      const active = workspaces.some(w => tasks.some(t => t.workspace_id === w.workspace_id && t.task_id === selectedId));
      const open = expanded[repository.repository_id] !== false;
      return <section className={`repository-group ${active ? 'has-selection' : ''}`} key={repository.repository_id}>
        <div className="repository-group-heading"><button aria-expanded={open} aria-label={`Toggle ${repository.name}`} onClick={() => toggle(repository.repository_id)}>{open ? '▾' : '▸'}</button><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link></div>
        {workspaces.map(workspace => {
          const group = tasks.filter(t => t.workspace_id === workspace.workspace_id);
          const selected = group.some(t => t.task_id === selectedId);
          if (!open && !selected) return null;
          const key = `workspace:${workspace.workspace_id}`;
          const workspaceOpen = expanded[key] !== false;
          const label = workspaceLabel(workspace, data.tasks);
          return <section className="workspace-group" key={workspace.workspace_id} aria-label={label}>
            <div className="repository-group-heading workspace-group-heading">
              <button aria-expanded={workspaceOpen} aria-label={`Toggle ${label}`} onClick={() => toggle(key)}>{workspaceOpen ? '▾' : '▸'}</button>
              <Link to={`/workspaces/${workspace.workspace_id}`} title={`${label} · ${workspace.branch} · ${workspace.path}`}><span>{label}</span><small>{workspace.branch || 'Detached HEAD'}{workspace.status !== 'active' && ` · ${workspace.status}`}</small></Link>
            </div>
            {(open && workspaceOpen ? group : group.filter(t => t.task_id === selectedId)).map(task => <TaskRow key={task.task_id} task={task} selected={task.task_id === selectedId} compact />)}
            {open && workspaceOpen && <>
              {group.length === 0 && <p className="workspace-empty">{workspace.status === 'active' ? 'No tasks yet' : 'No recent tasks'}</p>}
              <Link className="browse-tasks" to={`/workspaces/${workspace.workspace_id}`}>Browse all tasks</Link>
            </>}
          </section>;
        })}
        {open && workspaces.length === 0 && <p>No worktrees yet.</p>}
      </section>;
    })}
    {error && <p role="alert" className="error">{error} · {data ? 'Showing saved navigation. Retrying automatically.' : 'Retrying automatically.'}</p>}
    {selected.error && <p role="alert" className="error">Selected task: {selected.error} · Retrying automatically.</p>}
    <div className="secondary-navigation"><Link to="/">Repositories / Add repository</Link><Link to="/monitor">Service monitor</Link></div>
  </nav></aside>;
}
