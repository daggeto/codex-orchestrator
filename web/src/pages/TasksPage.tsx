import { useState } from 'react';
import { Link, useSearchParams } from 'react-router';
import { post } from '../api';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import type { TaskRecord } from '../types';
import { TaskList } from '../components/TaskList';
import { Button } from '../components/ui/Button';
import { ResourceState } from '../components/ui/ResourceState';
export function TasksPage() {
  const [params, setParams] = useSearchParams();
  const archived = params.get('tab') === 'archive';
  const { data: navigation } = useNavigation();
  const [pending, setPending] = useState('');
  const [failure, setFailure] = useState('');
  const query = new URLSearchParams({ archived: archived ? 'only' : 'exclude', limit: '50' });
  for (const key of ['repository_id', 'status', 'before']) if (params.get(key)) query.set(key, params.get(key)!);
  const { data, error, refresh } = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/tasks?${query}`);
  function filter(key: string, value: string) { const next = new URLSearchParams(params); next.delete('before'); value ? next.set(key, value) : next.delete(key); setParams(next); }
  return <section className="tasks-page"><h1>{archived ? 'Archive' : 'Tasks'}</h1><nav className="task-tabs" aria-label="Task views"><Link aria-current={!archived ? 'page' : undefined} to="/tasks">Tasks</Link><Link aria-current={archived ? 'page' : undefined} to="/tasks?tab=archive">Archive</Link></nav>
    <div className="task-filters"><label>Repository<select value={params.get('repository_id') || ''} onChange={e => filter('repository_id', e.target.value)}><option value="">All repositories</option>{navigation?.repositories.map(repo => <option key={repo.repository_id} value={repo.repository_id}>{repo.name}</option>)}</select></label>{!archived && <label className="check"><input type="checkbox" checked={params.get('status') === 'open'} onChange={e => filter('status', e.target.checked ? 'open' : '')} />Open tasks</label>}</div>
    {!data && !error && <ResourceState>Loading tasks…</ResourceState>}{error && <ResourceState error>{error}{data && ' · Showing saved results.'}</ResourceState>}
    {data?.tasks.length === 0 && <ResourceState>{archived ? 'No archived tasks. Archive a finished task from its task page.' : 'No tasks match these filters.'}</ResourceState>}
    {data && <TaskList tasks={data.tasks} actions={archived ? task => <Button aria-label={`Restore ${task.title}`} disabled={!!pending} onClick={async () => { if (pending) return; setPending(task.task_id); setFailure(''); try { await post(`/api/tasks/${encodeURIComponent(task.task_id)}/archive`, { archived: false }); refresh(); } catch (e) { setFailure(String(e)); } finally { setPending(''); } }}>{pending === task.task_id ? 'Restoring…' : 'Restore'}</Button> : undefined} />}
    {failure && <p role="alert" className="error">{failure}</p>}<div className="task-controls">{params.get('before') && <Button onClick={() => filter('before', '')}>Newest</Button>}{data?.next_cursor && <Button onClick={() => { const next = new URLSearchParams(params); next.set('before', data.next_cursor!); setParams(next); }}>Older tasks</Button>}</div>
  </section>;
}
