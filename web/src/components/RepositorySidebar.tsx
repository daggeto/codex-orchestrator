import { Link, useLocation } from 'react-router';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import type { TaskView } from '../types';
import { TaskRow } from './TaskRow';
export function RepositorySidebar() {
  const { data, error, expanded, toggle } = useNavigation();
  const { pathname, search } = useLocation();
  const selectedId = pathname.startsWith('/tasks/') ? decodeURIComponent(pathname.slice(7)) : pathname.startsWith('/agents/') ? new URLSearchParams(search).get('task_id') : null;
  // Fetch detail independently of the capped preview, including on contextual agent routes.
  const selected = useResource<TaskView>(selectedId ? `/api/tasks/${encodeURIComponent(selectedId)}` : null, selectedId ? 3000 : 0);
  const tasks = [...(data?.tasks || [])];
  if (selectedId && selected.data?.task.task_id === selectedId && !tasks.some(t => t.task_id === selectedId)) tasks.unshift(selected.data.task);
  return <aside className="repository-sidebar"><Link className="brand" to="/"><span className="brand-mark" aria-hidden="true">O</span>Orchestrator</Link><nav aria-label="Task navigation">
    <div className="task-tabs"><Link to="/tasks" aria-current={pathname === '/tasks' && !search.includes('archive') ? 'page' : undefined}>Tasks</Link><Link to="/tasks?tab=archive" aria-current={search.includes('tab=archive') ? 'page' : undefined}>Archive</Link></div>
    {data?.repositories.map(repository => { const workspaces = data.workspaces.filter(w => w.repository_id === repository.repository_id); const group = tasks.filter(t => workspaces.some(w => w.workspace_id === t.workspace_id)); const active = group.some(t => t.task_id === selectedId); return <section className={`repository-group ${active ? 'has-selection' : ''}`} key={repository.repository_id}>
      <div className="repository-group-heading"><button aria-expanded={expanded[repository.repository_id] !== false} aria-label={`Toggle ${repository.name}`} onClick={() => toggle(repository.repository_id)}>{expanded[repository.repository_id] === false ? '▸' : '▾'}</button><Link to={`/repositories/${repository.repository_id}`}>{repository.name}</Link></div>
      {(expanded[repository.repository_id] !== false ? group : group.filter(t => t.task_id === selectedId)).map(task => <TaskRow key={task.task_id} task={task} selected={task.task_id === selectedId} workspace={workspaces.find(w => w.workspace_id === task.workspace_id)?.branch || 'Main'} />)}
      {expanded[repository.repository_id] !== false && <Link className="browse-tasks" to={`/tasks?repository_id=${repository.repository_id}`}>Browse all tasks</Link>}
    </section>; })}
    {error && <p role="alert" className="error">{error}</p>}
    <div className="secondary-navigation"><Link to="/">Repositories / Add repository</Link><Link to="/monitor">Service monitor</Link></div>
  </nav></aside>;
}
