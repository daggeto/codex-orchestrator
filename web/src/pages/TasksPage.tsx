import { EmptyState } from '../components/ui/EmptyState';
import { taskListUrl } from '../taskUrls';
import { Checkbox } from '../components/ui/primitives/checkbox';
import { NativeSelect } from '../components/ui/primitives/native-select';
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
  const [feedback, setFeedback] = useState('');
  const query = new URLSearchParams({ archived: archived ? 'only' : 'exclude', limit: '50' });
  for (const key of ['repository_id', 'status', 'before']) if (params.get(key)) query.set(key, params.get(key)!);
  const { data, error, refresh } = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/tasks?${query}`);
  function filter(key: string, value: string) { const next = new URLSearchParams(params); next.delete('before'); value ? next.set(key, value) : next.delete(key); setParams(next); }
  return <section className="tasks-page"><h1>{archived ? 'Archive' : 'Tasks'}</h1><nav className="task-tabs" aria-label="Task views"><Link aria-current={!archived ? 'page' : undefined} to={taskListUrl(params.get('repository_id'))}>Tasks</Link><Link aria-current={archived ? 'page' : undefined} to={taskListUrl(params.get('repository_id'), true)}>Archive</Link></nav>
    <div className="task-filters"><label>Repository<NativeSelect value={params.get('repository_id') || ''} onChange={e => filter('repository_id', e.target.value)}><option value="">All repositories</option>{navigation?.repositories.map(repo => <option key={repo.repository_id} value={repo.repository_id}>{repo.name}</option>)}</NativeSelect></label>{!archived && <label className="check"><Checkbox checked={params.get('status') === 'open'} onCheckedChange={checked => filter('status', checked ? 'open' : '')} />Open tasks</label>}</div>
    {!data && !error && <ResourceState>Loading tasks…</ResourceState>}{error && <ResourceState error>{error}{data && ' · Showing saved results.'}</ResourceState>}
    {data?.tasks.length === 0 && <EmptyState title={archived ? 'No archived tasks' : 'No tasks match these filters'}>{archived && 'Archive a finished task from its task page.'}{(params.has('repository_id') || params.has('status')) && <Link to={taskListUrl(null, archived)}>Clear filters</Link>}</EmptyState>}
    {data && <TaskList tasks={data.tasks} actions={archived ? task => <Button aria-label={`Restore ${task.title}`} disabled={!!pending || !!error} onClick={async () => { if (pending) return; setPending(task.task_id); setFailure(''); try { await post(`/api/tasks/${encodeURIComponent(task.task_id)}/archive`, { archived: false }); setFeedback(`Restored ${task.title}.`); refresh(); } catch (e) { setFailure(`${String(e)}. Restore acceptance is unconfirmed. Refresh task results before deciding whether to try again.`); refresh(); } finally { setPending(''); } }}>{pending === task.task_id ? 'Restoring…' : 'Restore'}</Button> : undefined} />}
    {feedback && <p role="status">{feedback}</p>}
    {failure && <p role="alert" className="error">{failure} <Button onClick={refresh}>Refresh tasks</Button></p>}<div className="task-controls">{params.get('before') && <Button onClick={() => filter('before', '')}>Newest</Button>}{data?.next_cursor && <Button onClick={() => { const next = new URLSearchParams(params); next.set('before', data.next_cursor!); setParams(next); }}>Older tasks</Button>}</div>
  </section>;
}
