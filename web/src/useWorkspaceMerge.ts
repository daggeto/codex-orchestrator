import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post } from './api';
import { useResource } from './useResource';
import type { MergeEligibility, MergeOperation, MergeRequest } from './types';

const key = (id: string) => `workspace-merge:${id}`;
const terminal = (operation: MergeOperation) => ['merged', 'already_integrated', 'failed'].includes(operation.state);
function saved(id: string): MergeRequest | null {
  try {
    const value = JSON.parse(localStorage.getItem(key(id)) || 'null') as MergeRequest | null;
    return value && ['operation_id', 'source_branch', 'source_sha', 'destination_workspace_id', 'destination_path', 'destination_sha']
      .every(field => typeof value[field as keyof MergeRequest] === 'string' && value[field as keyof MergeRequest]) ? value : null;
  } catch { return null; }
}

export function useWorkspaceMerge(workspaceId: string) {
  const current = useRef(workspaceId);
  const mounted = useRef(true);
  current.current = workspaceId;
  const [action, setAction] = useState<{ id: string; pending: MergeRequest | null; busy: boolean; error: string }>(
    () => ({ id: workspaceId, pending: saved(workspaceId), busy: false, error: '' }));
  useEffect(() => {
    mounted.current = true;
    setAction({ id: workspaceId, pending: saved(workspaceId), busy: false, error: '' });
    const sync = (event: StorageEvent) => {
      if (event.key === key(workspaceId)) setAction(previous => ({ ...previous, pending: saved(workspaceId) }));
    };
    window.addEventListener('storage', sync);
    return () => { mounted.current = false; window.removeEventListener('storage', sync); };
  }, [workspaceId]);
  const state = action.id === workspaceId ? action : { id: workspaceId, pending: null, busy: false, error: '' };
  const endpoint = `/api/workspaces/${encodeURIComponent(workspaceId)}`;
  const eligibility = useResource<MergeEligibility>(`${endpoint}/merge`);
  const attempt = useResource<{ operation: MergeOperation }>(state.pending
    ? `${endpoint}/merges/${encodeURIComponent(state.pending.operation_id)}` : null);
  const [accepted, setAccepted] = useState<{ id: string; operation: MergeOperation } | null>(null);
  const ownOperation = attempt.data?.operation || (accepted?.id === workspaceId &&
    (!state.pending || accepted.operation.operation_id === state.pending.operation_id) ? accepted.operation : null);
  const latest = eligibility.data?.latest_operation;
  // A newer externally accepted operation supersedes a completed local attempt.
  const operation = latest && (!ownOperation || terminal(ownOperation) && latest.created_at > ownOperation.created_at)
    ? latest : ownOperation || latest;
  useEffect(() => {
    if (ownOperation && terminal(ownOperation) && state.pending?.operation_id === ownOperation.operation_id) {
      try {
        const stored = saved(workspaceId);
        if (stored?.operation_id === ownOperation.operation_id) localStorage.removeItem(key(workspaceId));
      } catch { /* Keep polling; an unavailable storage does not change the outcome. */ }
    }
  }, [ownOperation, state.pending, workspaceId]);
  const unresolved = operation && !terminal(operation);
  const uncertain = !!state.pending && !ownOperation;
  const busy = state.busy || !!unresolved || uncertain;

  async function merge() {
    const id = workspaceId;
    if (state.busy) return;
    const active = () => mounted.current && current.current === id;
    setAction(previous => ({ ...previous, busy: true, error: '' }));
    let request = state.pending;
    try {
      if (request) {
        try {
          const found = await get<{ operation: MergeOperation }>(`${endpoint}/merges/${encodeURIComponent(request.operation_id)}`);
          if (!active()) return;
          setAccepted({ id, operation: found.operation });
          if (!terminal(found.operation)) return;
          // A terminal attempt is immutable. New commits/retries get a new ID.
          request = null;
        } catch (reason) {
          if (!(reason instanceof ApiError) || reason.status !== 404) throw reason;
          // The original POST may never have reached the service. Reuse exactly its ID and snapshot.
        }
      }
      if (!request) {
        const check = await get<MergeEligibility>(`${endpoint}/merge`);
        if (!active()) return;
        if (!check.eligible || !check.source_branch || !check.source_sha || !check.destination_workspace_id || !check.destination_path || !check.destination_sha) {
          throw new Error(check.blockers.join('; ') || 'Merge eligibility is unavailable');
        }
        // Do not silently merge newer commits than those displayed at click time.
        const displayed = eligibility.data;
        if (!displayed || ['source_branch', 'source_sha', 'destination_workspace_id', 'destination_path', 'destination_sha']
          .some(field => check[field as keyof MergeEligibility] !== displayed[field as keyof MergeEligibility])) {
          throw new Error('Source or local main changed. Wait for the displayed details to refresh, then retry.');
        }
        request = { operation_id: crypto.randomUUID(), source_branch: check.source_branch, source_sha: check.source_sha,
          destination_workspace_id: check.destination_workspace_id, destination_path: check.destination_path, destination_sha: check.destination_sha };
      }
      if (!active()) return;
      // Persist before sending. Failure to persist prevents an unrecoverable submission.
      localStorage.setItem(key(id), JSON.stringify(request));
      setAction({ id, pending: request, busy: true, error: '' });
      const response = await post<{ operation: MergeOperation }>(`${endpoint}/merge`, request);
      if (active()) setAccepted({ id, operation: response.operation });
    } catch (reason) {
      if (active()) {
        // Explicit rejection means no operation was accepted. Transport errors remain pending.
        if (reason instanceof ApiError && [400, 404, 409].includes(reason.status)) {
          try { localStorage.removeItem(key(id)); } catch { /* The saved ID is still safe to reconcile. */ }
          setAction({ id, pending: null, busy: false, error: String(reason) });
        } else setAction(previous => ({ ...previous, error: `${String(reason)}. Check the saved attempt before retrying.` }));
      }
    } finally {
      if (active()) setAction(previous => ({ ...previous, busy: false }));
    }
  }
  return { eligibility, operation, busy, submitting: state.busy, uncertain,
    error: state.error || eligibility.error || (uncertain ? attempt.error : ''), merge };
}
