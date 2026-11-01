import { useEffect, useState } from 'react';
import { Link, useParams } from 'react-router';
import { TaskComposer } from '../components/TaskComposer';
import { useNavigation } from '../navigation';
import { useResource } from '../useResource';
import { useWorkspaceMerge } from '../useWorkspaceMerge';
import { Badge } from '../components/ui/Badge';
import { Button } from '../components/ui/Button';
import { Card } from '../components/ui/Card';
import { PageHeader } from '../components/ui/PageHeader';
import { ResourceState } from '../components/ui/ResourceState';
import type { TaskRecord } from '../types';

export function WorkspacePage() {
  const { workspaceId } = useParams();
  return <WorkspaceDetails key={workspaceId} workspaceId={workspaceId || ''} />;
}

function WorkspaceDetails({ workspaceId }: { workspaceId: string }) {
  const merge = useWorkspaceMerge(workspaceId);
  const { data, error } = useNavigation();
  const workspace = data?.workspaces.find(item => item.workspace_id === workspaceId);
  const [before, setBefore] = useState('');
  useEffect(() => { setBefore(''); }, [workspaceId]);
  const history = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/workspaces/${workspaceId}/tasks?before=${encodeURIComponent(before)}`);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  if (!workspace) return <ResourceState error={!!error}>{error || (data ? 'Workspace not found' : 'Loading workspace…')}</ResourceState>;
  return <><PageHeader eyebrow={<Link to={`/repositories/${workspace.repository_id}`}>Repository</Link>} title={workspace.kind === 'main' ? 'Main' : workspace.branch || 'Worktree'} description={<code>{workspace.path}</code>} actions={<Badge>{workspace.status}</Badge>} />
    {error && <ResourceState error>{error} · Navigation may be stale.</ResourceState>}
    {workspace.status === 'active' && <TaskComposer key={workspace.workspace_id} workspace={workspace} />}
    <Card><h2>Workspace details</h2><p className="muted">Checkout: {workspace.kind === 'main' ? 'Main' : 'Worktree'}</p>
    {workspace.kind === 'worktree' && workspace.status !== 'archived' && <div className="workspace-merge">
      <p>Source: <code>{merge.eligibility.data?.source_branch || workspace.branch}</code>{' '}
        {merge.eligibility.data?.source_sha && <code>{merge.eligibility.data.source_sha}</code>}</p>
      <p>Destination: local <code>main</code>{' '}
        {merge.eligibility.data?.destination_sha && <code>{merge.eligibility.data.destination_sha}</code>}
        <br /><code>{merge.eligibility.data?.destination_path || 'No verified main checkout'}</code></p>
      <Button disabled={merge.submitting || (!merge.uncertain && (merge.busy || !merge.eligibility.data?.eligible)) || busy}
        onClick={() => void merge.merge()}>{merge.submitting || ['accepted', 'running'].includes(merge.operation?.state || '') ? 'Merging…' : merge.uncertain ? 'Check saved merge attempt' : 'Merge to main'}</Button>
      {merge.eligibility.data?.blockers.map(blocker => <p className="muted" key={blocker}>{blocker}</p>)}
      {merge.error && <ResourceState error>{merge.error}</ResourceState>}
      {merge.uncertain && <ResourceState>Checking the saved merge attempt. Reconcile it before retrying or archiving.</ResourceState>}
      {merge.operation && <ResourceState error={['failed', 'recovery_required'].includes(merge.operation.state)}>
        {['accepted', 'running'].includes(merge.operation.state) ? 'Merging…' :
          merge.operation.state === 'merged' ? `Merged into local main at ${merge.operation.destination_path}. Commit: ${merge.operation.resulting_sha}.` :
          merge.operation.state === 'already_integrated' ? `Already integrated into local main at ${merge.operation.destination_path}. Commit: ${merge.operation.resulting_sha}.` :
          merge.operation.error_message || 'Merge failed.'}
        <p>Attempt source: <code>{merge.operation.source_branch}</code> at <code>{merge.operation.source_sha}</code></p>
        {!!merge.operation.conflict_paths.length && <p>Conflicts: {merge.operation.conflict_paths.join(', ')}</p>}
        {merge.operation.diagnostics && <details><summary>Git diagnostics</summary><pre>{merge.operation.diagnostics}</pre></details>}
      </ResourceState>}
    </div>}
    {workspace.archive && <p>Archive: {workspace.archive.stage} {workspace.archive.path} {workspace.archive.error}</p>}
    {workspace.kind === 'worktree' && !['archived','archiving'].includes(workspace.status) && <Button variant="danger" disabled={busy || merge.busy || ['merging', 'merge_recovery_required'].includes(workspace.status)} onClick={async () => {
      setBusy(true); setFailure('');
      try {
        const response = await fetch(`/api/workspaces/${workspace.workspace_id}`, { method: 'DELETE' });
        if (!response.ok) { const result = await response.json(); throw new Error(result.error || 'Archive failed'); }
      } catch (reason) { setFailure(String(reason)); } finally { setBusy(false); }
    }}>{busy ? 'Saving archive…' : 'Archive and delete worktree'}</Button>}
    {failure && <p className="error" role="alert">{failure}</p>}
    </Card>
    <Card><h2>Task history</h2>
    {!history.data && !history.error && <ResourceState>Loading task history…</ResourceState>}
    {history.data?.tasks.length === 0 && <ResourceState>No tasks in this workspace yet.</ResourceState>}
    <ul className="resource-list">{history.data?.tasks.map(task => <li key={task.task_id}><Link to={`/tasks/${encodeURIComponent(task.task_id)}`}>{task.title}</Link><Badge>{task.status}</Badge></li>)}</ul>
    {history.error && <p className="error">{history.error}</p>}
    {before && <button onClick={() => setBefore('')}>Newest tasks</button>}
    {history.data?.tasks.length === 50 && <button onClick={() => setBefore(history.data!.next_cursor || '')}>Older tasks</button>}
    </Card>
  </>;
}
