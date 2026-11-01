import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { TaskComposer } from '../components/TaskComposer';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';
import type { TaskRecord } from '../types';

export function WorkspacePage() {
  const { workspaceId } = useParams();
  const { data, error } = useNavigation();
  const workspace = data?.workspaces.find(item => item.workspace_id === workspaceId);
  const [before, setBefore] = useState('');
  useEffect(() => { setBefore(''); }, [workspaceId]);
  const history = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/workspaces/${workspaceId}/tasks${before ? `?before=${encodeURIComponent(before)}` : ''}`);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  if (!workspace) return <ResourceState error={!!error}>{error || (data ? 'Workspace not found' : 'Loading workspace…')}</ResourceState>;
  return <><PageHeader eyebrow={<Link to={`/repositories/${workspace.repository_id}`}>Repository</Link>} title={workspace.kind === 'main' ? 'Main' : workspace.branch || 'Worktree'} description={<code>{workspace.path}</code>} actions={<Badge>{workspace.status}</Badge>} />
    {error && <ResourceState error>{error} · Navigation may be stale.</ResourceState>}
    {workspace.status === 'active' && <TaskComposer key={workspace.workspace_id} workspace={workspace} />}
    <Card><h2>Workspace details</h2><p className="muted">Checkout: {workspace.kind === 'main' ? 'Main' : 'Worktree'}</p>
    {workspace.archive && <p>Archive: {workspace.archive.stage} {workspace.archive.path} {workspace.archive.error}</p>}
    {workspace.kind === 'worktree' && !['archived','archiving'].includes(workspace.status) && <Button variant="danger" disabled={busy} onClick={async () => {
      if (busy || !window.confirm(`Remove worktree ${workspace.path}? This cancels unfinished tasks in this workspace and removes its checkout. Task history is retained. Restoring a task does not recreate the checkout.`)) return;
      setBusy(true); setFailure('');
      try {
        const response = await fetch(`/api/workspaces/${workspace.workspace_id}`, { method: 'DELETE' });
        if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Archive failed'); }
      } catch (reason) { setFailure(String(reason)); } finally { setBusy(false); }
    }}>{busy ? 'Saving archive…' : 'Remove worktree'}</Button>}
    {failure && <p className="error" role="alert">{failure}</p>}
    </Card>
    <Card><h2>Task history</h2>
    {!history.data && !history.error && <ResourceState>Loading task history…</ResourceState>}
    {history.data?.tasks.length === 0 && <ResourceState>No tasks in this workspace yet.</ResourceState>}
    <ul className="resource-list">{history.data?.tasks.map(task => <li key={task.task_id}><Link to={`/tasks/${encodeURIComponent(task.task_id)}`}>{task.title}</Link><Badge>{task.status}</Badge></li>)}</ul>
    {history.error && <p className="error">{history.error}</p>}
    {before && <button onClick={() => setBefore('')}>Newest tasks</button>}
    {history.data?.next_cursor && <button onClick={() => setBefore(history.data!.next_cursor || '')}>Older tasks</button>}
    </Card>
  </>;
}
