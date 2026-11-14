import { taskListUrl } from '../taskUrls';
import { Button } from './ui/Button';
import { Link, useLocation } from 'react-router';
import { useNavigation } from '../navigation';
import { TaskRow } from './TaskRow';
import { workspaceLabel } from '../workspaceLabel';
export function RepositorySidebar() {
  const { data, error, expanded, toggle, selected, selectedId } = useNavigation();
  const { pathname, search } = useLocation();
  const tasks = [...(data?.tasks || [])];
  if (selectedId && selected.data?.task.task_id === selectedId && !tasks.some(t => t.task_id === selectedId)) tasks.unshift(selected.data.task);
  const selectedTask = tasks.find(task => task.task_id === selectedId);
  const contextWorkspace = data?.workspaces.find(workspace => workspace.workspace_id === selectedTask?.workspace_id || pathname === `/workspaces/${workspace.workspace_id}`);
  const repositoryId = pathname.startsWith('/repositories/') ? pathname.slice('/repositories/'.length) : contextWorkspace?.repository_id || new URLSearchParams(search).get('repository_id');
  const waiting = tasks.filter(task => task.status === 'waiting_input' && !task.archived_at);
  return <aside className="repository-sidebar"><Link className="brand" to="/"><span className="brand-mark" aria-hidden="true">O</span>Orchestrator</Link><nav aria-label="Task navigation">
    <div className="task-tabs"><Link to={taskListUrl(repositoryId)} aria-current={pathname === '/tasks' && !search.includes('archive') ? 'page' : undefined}>Tasks</Link><Link to={taskListUrl(repositoryId, true)} aria-current={search.includes('tab=archive') ? 'page' : undefined}>Archive</Link></div>
    {!!waiting.length && <section aria-label="Waiting for you"><h2>Waiting for you</h2>{waiting.map(task => <TaskRow key={task.task_id} task={task} selected={task.task_id === selectedId} compact />)}</section>}
    {!data && !error && <p role="status">Loading repositories…</p>}
    {data?.repositories.length === 0 && <p>No repositories yet.</p>}
    {data?.repositories.map(repository => {
      const workspaces = data.workspaces.filter(w => w.repository_id === repository.repository_id);
      const active = workspaces.some(w => tasks.some(t => t.workspace_id === w.workspace_id && t.task_id === selectedId));
      const open = expanded[repository.repository_id] !== false;
      return <section className={`repository-group ${active ? 'has-selection' : ''}`} key={repository.repository_id}>
        <div className="repository-group-heading"><Button aria-expanded={open} aria-label={`Toggle ${repository.name}`} onClick={() => toggle(repository.repository_id)}>{open ? '▾' : '▸'}</Button><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link></div>
        {workspaces.map(workspace => {
          const group = tasks.filter(t => t.workspace_id === workspace.workspace_id);
          const selected = group.some(t => t.task_id === selectedId);
          const needsInput = group.some(t => t.status === 'waiting_input' && !t.archived_at);
          if (!open && !selected && !needsInput) return null;
          const key = `workspace:${workspace.workspace_id}`;
          const workspaceOpen = expanded[key] !== false;
          const label = workspaceLabel(workspace);
          return <section className="workspace-group" key={workspace.workspace_id} aria-label={label}>
            <div className="repository-group-heading workspace-group-heading">
              <Button aria-expanded={workspaceOpen} aria-label={`Toggle ${label}`} onClick={() => toggle(key)}>{workspaceOpen ? '▾' : '▸'}</Button>
              <Link to={`/workspaces/${workspace.workspace_id}`} title={`${label} · ${workspace.branch} · ${workspace.path}`}><span>{label}</span><small>{workspace.branch || 'Detached HEAD'}{workspace.status !== 'active' && ` · ${workspace.status}`}</small></Link>
            </div>
            {(open && workspaceOpen ? group : group.filter(t => t.task_id === selectedId || (t.status === 'waiting_input' && !t.archived_at))).map(task => <TaskRow key={task.task_id} task={task} selected={task.task_id === selectedId} compact />)}
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
    <div className="secondary-navigation"><Link to="/">Repositories / Add repository</Link><Link to="/feedback">Feedback Loop</Link><Link to="/monitor">Service monitor</Link></div>
  </nav></aside>;
}
