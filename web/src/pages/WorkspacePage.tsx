import { workspaceLabel } from '../workspaceLabel';
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
  const history = useResource<{ tasks: TaskRecord[]; next_cursor: string | null }>(`/api/workspaces/${workspaceId}/tasks${before ? `?before=${encodeURIComponent(before)}` : ''}`);
  const [failure, setFailure] = useState('');
  const [busy, setBusy] = useState(false);
  if (!workspace) return <ResourceState error={!!error}>{error || (data ? 'Workspace not found' : 'Loading workspace…')}</ResourceState>;
  return <><PageHeader eyebrow={<Link to={`/repositories/${workspace.repository_id}`}>Repository</Link>} title={workspaceLabel(workspace)} description={<code>{workspace.path}</code>} actions={<Badge>{workspace.status}</Badge>} />
    {error && <ResourceState error>{error} · Navigation may be stale.</ResourceState>}
    {workspace.status === 'active' && <TaskComposer key={workspace.workspace_id} workspace={workspace} />}
    <Card><h2>Workspace details</h2><p>Branch: <code>{workspace.branch || 'Detached HEAD'}</code></p><p className="muted">Checkout: {workspace.kind === 'main' ? 'Main' : 'Worktree'}</p>
    {workspace.kind === 'worktree' && workspace.status !== 'archived' && <div className="workspace-merge">
      <p>Source: <code>{merge.eligibility.data?.source_branch || workspace.branch}</code>{' '}
        {merge.eligibility.data?.source_sha && <code>{merge.eligibility.data.source_sha}</code>}</p>
      <p>Destination: local <code>main</code>{' '}
        {merge.eligibility.data?.destination_sha && <code>{merge.eligibility.data.destination_sha}</code>}
        <br /><code>{merge.eligibility.data?.destination_path || 'No verified main checkout'}</code></p>
      {merge.eligibility.data?.already_integrated && !merge.busy
        ? <p className="muted" role="status">Already merged into local main. No new changes to merge.</p>
        : <Button disabled={merge.submitting || (!merge.uncertain && (merge.busy || !(merge.eligibility.data?.eligible || merge.eligibility.data?.can_commit))) || busy}
        onClick={() => void merge.merge()}>{merge.submitting || ['accepted', 'running'].includes(merge.operation?.state || '')
          ? merge.operation?.phase === 'committing' ? 'Committing changes…' : 'Merging…'
          : merge.uncertain ? 'Check saved merge attempt' : merge.eligibility.data?.can_commit ? 'Commit and merge' : 'Merge to main'}</Button>}
      {merge.eligibility.data && merge.eligibility.data.can_commit === undefined && <p className="muted">Restart the orchestrator service to load Commit and merge. Eligibility updates automatically.</p>}
      {merge.eligibility.data?.blockers.map(blocker => <p className="muted" key={blocker}>{blocker}</p>)}
      {!!merge.eligibility.data?.dirty_checkouts?.length && <div>
        <p>Commit and merge commits all listed tracked and untracked changes in each checkout below, using an AI-generated message for each commit, then merges into local main. Ignored files are excluded.</p>
        {merge.eligibility.data.dirty_checkouts.map(checkout => <div key={checkout.path}>
          <p>Changes in {checkout.path === merge.eligibility.data?.destination_path ? 'destination main' : 'source worktree'}: <code>{checkout.path}</code></p>
          <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{checkout.status}</pre>
          <p><code>M</code> = modified, <code>A</code> = added, <code>D</code> = deleted, <code>??</code> = untracked. The first column shows staged changes; the second shows unstaged changes.</p>
        </div>)}
      </div>}
      {merge.error && <ResourceState error>{merge.error}</ResourceState>}
      {merge.uncertain && <ResourceState>Checking the saved merge attempt. Reconcile it before retrying or archiving.</ResourceState>}
      {merge.operation && <ResourceState error={['failed', 'recovery_required'].includes(merge.operation.state)}>
        {['accepted', 'running'].includes(merge.operation.state) ? merge.operation.phase === 'committing' ? 'Generating commit messages and committing changes…' : 'Merging…' :
          merge.operation.state === 'merged' ? `Merged into local main at ${merge.operation.destination_path}. Commit: ${merge.operation.resulting_sha}.` :
          merge.operation.state === 'already_integrated' ? `Already integrated into local main at ${merge.operation.destination_path}. Commit: ${merge.operation.resulting_sha}.` :
          merge.operation.error_message || 'Merge failed.'}
        <p>Attempt source: <code>{merge.operation.source_branch}</code> at <code>{merge.operation.source_sha}</code></p>
        {merge.operation.commits?.map(commit => <p key={commit.sha}>Committed <code>{commit.sha}</code>: {commit.message}<br /><code>{commit.path}</code></p>)}
        {!!merge.operation.conflict_paths.length && <p>Conflicts: {merge.operation.conflict_paths.join(', ')}</p>}
        {merge.operation.diagnostics && <details><summary>Git diagnostics</summary><pre>{merge.operation.diagnostics}</pre></details>}
      </ResourceState>}
    </div>}
    {workspace.archive && <p>Archive: {workspace.archive.stage} {workspace.archive.path} {workspace.archive.error}</p>}
    {workspace.kind === 'worktree' && !['archived','archiving'].includes(workspace.status) && <Button variant="danger" disabled={busy || merge.busy || ['merging', 'merge_recovery_required'].includes(workspace.status)} onClick={async () => {
      if (busy || !window.confirm(`Remove worktree ${workspace.path}? This cancels unfinished tasks in this workspace and removes its checkout. Task history is retained. All attachments for tasks owned by this worktree are permanently deleted, including archived tasks. Restoring history does not restore these files or recreate the checkout.`)) return;
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
