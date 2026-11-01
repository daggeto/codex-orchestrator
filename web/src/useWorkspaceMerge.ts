import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post } from './api';
import { useResource } from './useResource';
import type { MergeEligibility, MergeOperation, MergeRequest } from './types';

const key = (id: string) => `workspace-merge:${id}`;
const terminal = (operation: MergeOperation) => ['merged', 'already_integrated', 'failed'].includes(operation.state);
function reconcile(local: MergeOperation | null | undefined, observed: MergeOperation | null | undefined) {
  if (!local) return observed;
  if (!observed) return local;
  if (local.operation_id === observed.operation_id) {
    // Completed attempts are immutable; delayed responses cannot reopen them.
    if (terminal(local)) return local;
    return observed.state === 'accepted' ? local : observed;
  }
  return terminal(local) && observed.created_at > local.created_at ? observed : local;
}
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
  const [action, setAction] = useState<{ id: string; pending: MergeRequest | null; busy: boolean; error: string;
    transportWarning?: { operationId: string; message: string } }>(
    () => ({ id: workspaceId, pending: saved(workspaceId), busy: false, error: '' }));
  useEffect(() => {
    mounted.current = true;
    setAction({ id: workspaceId, pending: saved(workspaceId), busy: false, error: '' });
    const sync = (event: StorageEvent) => {
      // Removal in another tab is not an outcome. Keep polling until reconciled.
      if (event.key === key(workspaceId)) setAction(previous => ({ ...previous, pending: saved(workspaceId) || previous.pending }));
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
  function remember(id: string, incoming: MergeOperation) {
    setAccepted(previous => {
      if (previous?.id !== id) return { id, operation: incoming };
      // Reconcile against the current cache, not the response handler's snapshot.
      if (previous.operation.operation_id === incoming.operation_id) {
        return { id, operation: reconcile(previous.operation, incoming)! };
      }
      return previous.operation.created_at > incoming.created_at ? previous : { id, operation: incoming };
    });
  }
  const ownOperation = reconcile(accepted?.id === workspaceId &&
    (!state.pending || accepted.operation.operation_id === state.pending.operation_id) ? accepted.operation : null,
    attempt.data?.operation);
  const latest = eligibility.data?.latest_operation;
  // A newer externally accepted operation supersedes a completed local attempt.
  const operation = reconcile(ownOperation, latest);
  // Cleanup follows the saved request's outcome, independently of display order.
  const pendingOutcome = [ownOperation, latest].find(candidate => candidate && terminal(candidate) &&
    candidate.operation_id === state.pending?.operation_id);
  useEffect(() => {
    if (pendingOutcome) {
      // Preserve the result before stopping pending-operation polling.
      remember(workspaceId, pendingOutcome);
      setAction(previous => previous.pending?.operation_id === pendingOutcome.operation_id
        ? { ...previous, pending: null, transportWarning: undefined } : previous);
      try {
        const stored = saved(workspaceId);
        if (stored?.operation_id === pendingOutcome.operation_id) localStorage.removeItem(key(workspaceId));
      } catch { /* Keep polling; an unavailable storage does not change the outcome. */ }
    }
  }, [pendingOutcome, state.pending, workspaceId]);
  const unresolved = operation && !terminal(operation);
  const uncertain = !!state.pending && !ownOperation;
  const busy = state.busy || !!unresolved || uncertain;
  const transportWarning = state.transportWarning?.operationId === state.pending?.operation_id &&
    !pendingOutcome
    ? state.transportWarning?.message : '';

  async function merge() {
    const id = workspaceId;
    if (state.busy) return;
    const active = () => mounted.current && current.current === id;
    setAction(previous => ({ ...previous, busy: true, error: '', transportWarning: undefined }));
    let request = state.pending;
    let awaitingOutcome = !!request;
    try {
      if (request) {
        try {
          const found = await get<{ operation: MergeOperation }>(`${endpoint}/merges/${encodeURIComponent(request.operation_id)}`);
          if (!active()) return;
          remember(id, found.operation);
          // Checking a saved attempt never authorizes another merge.
          return;
        } catch (reason) {
          if (!(reason instanceof ApiError) || reason.status !== 404) throw reason;
          // The original POST may never have reached the service. Reuse exactly its ID and snapshot.
        }
      }
      if (!request) {
        const committing = !!eligibility.data?.can_commit;
        const check = await get<MergeEligibility>(`${endpoint}/merge`);
        if (!active()) return;
        if (!(committing ? check.can_commit : check.eligible) || !check.source_branch || !check.source_sha || !check.destination_workspace_id || !check.destination_path || !check.destination_sha) {
          throw new Error(check.blockers.join('; ') || 'Merge eligibility is unavailable');
        }
        // Do not silently merge newer commits than those displayed at click time.
        const displayed = eligibility.data;
        if (!displayed || ['source_branch', 'source_sha', 'destination_workspace_id', 'destination_path', 'destination_sha']
          .some(field => check[field as keyof MergeEligibility] !== displayed[field as keyof MergeEligibility])) {
          throw new Error('Source or local main changed. Wait for the displayed details to refresh, then retry.');
        }
        if (committing && (!check.changes || !displayed.changes ||
          Object.keys(check.changes).length !== Object.keys(displayed.changes).length ||
          Object.entries(check.changes).some(([id, tree]) => displayed.changes?.[id] !== tree))) {
          throw new Error('Working changes changed. Refresh the displayed files before committing.');
        }
        request = { operation_id: crypto.randomUUID(), source_branch: check.source_branch, source_sha: check.source_sha,
          destination_workspace_id: check.destination_workspace_id, destination_path: check.destination_path, destination_sha: check.destination_sha,
          ...(committing ? { commit_changes: true, changes: check.changes } : {}) };
      }
      if (!active()) return;
      // Persist before sending. Failure to persist prevents an unrecoverable submission.
      localStorage.setItem(key(id), JSON.stringify(request));
      setAction({ id, pending: request, busy: true, error: '' });
      awaitingOutcome = true;
      const response = await post<{ operation: MergeOperation }>(`${endpoint}/merge`, request);
      if (active()) remember(id, response.operation);
    } catch (reason) {
      if (active()) {
        // Explicit rejection means no operation was accepted. Transport errors remain pending.
        if (reason instanceof ApiError && [400, 404, 409].includes(reason.status)) {
          try { localStorage.removeItem(key(id)); } catch { /* The saved ID is still safe to reconcile. */ }
          setAction({ id, pending: null, busy: false, error: String(reason) });
        } else if (awaitingOutcome && request) {
          const operationId = request.operation_id;
          // Completion clears pending. Ignore failures arriving after reconciliation
          // or after another operation has replaced this request.
          setAction(previous => previous.pending?.operation_id === operationId
            ? { ...previous, transportWarning: { operationId,
              message: `${String(reason)}. Check the saved attempt before retrying.` } } : previous);
        } else setAction(previous => ({ ...previous, error: String(reason) }));
      }
    } finally {
      if (active()) setAction(previous => ({ ...previous, busy: false }));
    }
  }
  return { eligibility, operation, busy, submitting: state.busy, uncertain,
    error: state.error || transportWarning || eligibility.error || (uncertain ? attempt.error : ''), merge };
}
