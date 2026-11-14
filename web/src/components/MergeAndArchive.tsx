import { useRef, useState } from 'react';
import { post } from '../api';
import { useResource } from '../useResource';
import { useWorkspaceMerge } from '../useWorkspaceMerge';
import type { MergeOperation } from '../types';
import { Button } from './ui/Button';

export function MergeAndArchive({ workspaceId, disabled }: { workspaceId: string; disabled: boolean }) {
  const merge = useWorkspaceMerge(workspaceId);
  // Merge results are immutable in the hook. Cleanup has its own lifecycle.
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}`;
  const status = useResource<{ operation: MergeOperation }>(merge.operation
    ? `${endpoint}/merges/${encodeURIComponent(merge.operation.operation_id)}` : null);
  const operation = status.data?.operation ?? merge.operation;
  const check = merge.eligibility.data;
  const [error, setError] = useState('');
  const [retrying, setRetrying] = useState(false);
  const retryLock = useRef(false);
  const cleanup = operation?.archive_after_merge ? operation.cleanup_status : null;
  const succeeded = operation && ['merged', 'already_integrated'].includes(operation.state);
  const cleaning = succeeded && (cleanup === 'pending' || cleanup === 'running');
  const sourceDirty = check?.dirty_checkouts?.some(item => item.path !== check.destination_path);
  async function retry() {
    if (!operation || retryLock.current) return;
    retryLock.current = true; setRetrying(true); setError('');
    try { await post(`${endpoint}/merges/${encodeURIComponent(operation.operation_id)}/cleanup`, {}); }
    catch (reason) { setError(String(reason)); }
    finally { retryLock.current = false; setRetrying(false); status.refresh(); }
  }
  return <section className="workspace-merge" aria-label="Merge and archive">
    <h3>Merge worktree to main</h3>
    <p>Merges into project main, stops the test instance, saves a recovery archive, removes the worktree, and archives every task in it. Task conversations remain available. Worktree attachments are deleted.</p>
    {check?.destination_path && <p>Destination: <code>{check.destination_path}</code></p>}
    {check?.dirty_checkouts?.map(item => <div key={item.path}>
      <p>Uncommitted changes: <code>{item.path}</code></p>
      <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{item.status}</pre>
    </div>)}
    {check?.can_commit && <p>Commit and merge commits all listed changes, including changes in destination main.</p>}
    <Button disabled={disabled || merge.busy || merge.submitting || !!merge.eligibility.error || !!sourceDirty || !check?.eligible || !!cleaning || cleanup === 'completed'}
      onClick={() => void merge.merge(false, true)}>
      {merge.submitting || merge.busy ? 'Merging…' : 'Merge to main and archive'}
    </Button>
    {check?.can_commit && <Button disabled={disabled || merge.busy || merge.submitting || !!merge.eligibility.error || !!cleaning}
      onClick={() => void merge.merge(true, true)}>Commit, merge and archive</Button>}
    {merge.uncertain && <p role="status">Checking the saved merge attempt before continuing.</p>}
    {cleaning && <p role="status">Merged into main. Removing the worktree and archiving tasks…</p>}
    {cleanup === 'completed' && <p role="status">Merged into main. Worktree removed and tasks archived.</p>}
    {succeeded && cleanup === 'failed' && <div role="alert"><p>Merged into main, but cleanup needs attention: {operation.cleanup_error}</p>
      <Button disabled={disabled || retrying} onClick={() => void retry()}>{retrying ? 'Retrying…' : 'Retry cleanup'}</Button>
    </div>}
    {operation && ['failed', 'recovery_required'].includes(operation.state) && <p role="alert">{operation.error_message || 'Merge did not complete. Worktree retained.'}</p>}
    {check?.blockers.map(blocker => <p key={blocker}>{blocker}</p>)}
    {(error || merge.error || merge.eligibility.error || status.error) && <p role="alert">{error || merge.error || merge.eligibility.error || status.error}</p>}
  </section>;
}
