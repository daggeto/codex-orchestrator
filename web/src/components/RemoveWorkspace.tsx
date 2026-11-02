import { useRef, useState } from 'react';
import { ApiError, get } from '../api';
import { useResource } from '../useResource';
import { useNavigation } from '../navigation';
import type { WorkspaceRecord } from '../types';
import { Button } from './ui/Button';
import { AlertDialog, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger } from './ui/primitives/alert-dialog';
interface Removal { state: 'uncertain' | 'accepted'; archiveId?: string }
export function RemoveWorkspace({ workspace, disabled }: { workspace: WorkspaceRecord; disabled: boolean }) {
  const storage = `workspace-removal:${workspace.workspace_id}`;
  const navigation = useNavigation();
  const [open, setOpen] = useState(false);
  const [busy, setBusy] = useState(false);
  const lock = useRef(false);
  const cancel = useRef<HTMLButtonElement>(null);
  const [error, setError] = useState('');
  const [durable, setDurable] = useState(true);
  const [removal, setRemoval] = useState<Removal | null>(() => {
    try { const value = JSON.parse(sessionStorage.getItem(storage) || 'null'); return ['uncertain', 'accepted'].includes(value?.state) ? value : null; } catch { return null; }
  });
  const status = useResource<{ archive: { archive_id: string; stage: string; error?: string } }>(removal ? `/api/workspaces/${encodeURIComponent(workspace.workspace_id)}/archive` : null);
  function remember(value: Removal) {
    setRemoval(value);
    try { sessionStorage.setItem(storage, JSON.stringify(value)); } catch { setDurable(false); }
  }
  async function remove() {
    if (lock.current || disabled || removal) return;
    lock.current = true; setBusy(true); setError('');
    let sent = false;
    try {
      const latest = await get<{ workspaces: WorkspaceRecord[] }>('/api/navigation');
      const current = latest.workspaces.find(item => item.workspace_id === workspace.workspace_id);
      if (!current || current.kind !== 'worktree' || current.path !== workspace.path || ['archived', 'archiving', 'merging', 'merge_recovery_required'].includes(current.status) || current.available === false) {
        throw new Error('Workspace eligibility changed. Refresh its status before removing it.');
      }
      // This API has no client request identity. Persist uncertainty and send only once.
      remember({ state: 'uncertain' }); sent = true;
      const response = await fetch(`/api/workspaces/${encodeURIComponent(workspace.workspace_id)}`, { method: 'DELETE', signal: AbortSignal.timeout(15000) });
      const result = await response.json();
      if (!response.ok) throw new ApiError(result.error || 'Removal was not confirmed', response.status);
      if (typeof result.archive_id !== 'string') throw new Error('Archive identity was not returned');
      remember({ state: 'accepted', archiveId: result.archive_id }); setOpen(false); status.refresh(); navigation.refresh();
    } catch (reason) {
      setError(`${String(reason)}${sent ? '. Acceptance is unconfirmed. Check workspace status before taking another action.' : ''}`);
      navigation.refresh(); status.refresh();
    } finally { lock.current = false; setBusy(false); }
  }
  const matching = removal?.archiveId && status.data?.archive.archive_id === removal.archiveId;
  return <section aria-label="Worktree removal">
    {!['archived', 'archiving'].includes(workspace.status) && <AlertDialog open={open} onOpenChange={setOpen}>
      <AlertDialogTrigger render={<Button variant="danger" />} disabled={disabled || busy || !!removal}>Remove worktree</AlertDialogTrigger>
      <AlertDialogContent initialFocus={cancel}>
        <AlertDialogHeader><AlertDialogTitle>Remove this worktree?</AlertDialogTitle>
          <AlertDialogDescription>Remove the checkout at <code>{workspace.path}</code> and cancel unfinished tasks in this workspace. All attachments owned by its tasks, including archived tasks, are permanently deleted. Task history is retained. Restoring history does not restore files or recreate the checkout.</AlertDialogDescription></AlertDialogHeader>
        {error && <p role="alert" className="error">{error}</p>}
        <AlertDialogFooter><AlertDialogCancel ref={cancel} disabled={busy}>Cancel</AlertDialogCancel><Button variant="danger" disabled={disabled || busy || !!removal} onClick={() => void remove()}>{busy ? 'Requesting removal…' : 'Remove checkout and files'}</Button></AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>}
    {removal && <div role="status" className="operation-warning">
      {matching ? `Removal accepted. Cleanup stage: ${status.data!.archive.stage}.` : removal.state === 'accepted' ? 'Removal accepted. Waiting for its matching cleanup record.' : 'Removal acceptance is unconfirmed. This request will not be sent again automatically.'}
      {status.data && !matching && <p>Latest workspace archive: {status.data.archive.archive_id} · {status.data.archive.stage}. This record alone does not confirm your request.</p>}
      {matching && status.data?.archive.error && <p className="error">{status.data.archive.error}</p>}
      <Button onClick={() => { status.refresh(); navigation.refresh(); }}>Check workspace status</Button>
    </div>}
    {!durable && <p role="alert">Removal recovery is held only in memory. Keep this page open.</p>}
    {error && !open && <p role="alert" className="error">{error}</p>}
    {status.error && <p role="alert" className="error">Cleanup status unavailable: {status.error}</p>}
  </section>;
}
