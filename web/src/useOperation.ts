import { useCallback, useSyncExternalStore } from 'react';
import { prepareAttempt, reconcileAttempt, settleAttempt } from './operationState';
import type { OperationAttempt } from './operationState';

const records = new Map<string, OperationAttempt>();
const listeners = new Set<() => void>();
const failures = new Set<string>();
const prefix = 'orchestrator.operation.v1:';
function notify() { listeners.forEach(listener => listener()); }
function read(owner: string) {
  try {
    const raw = localStorage.getItem(prefix + owner);
    const saved = raw ? JSON.parse(raw) as OperationAttempt : undefined;
    if (saved && saved.owner === owner && typeof saved.id === 'string' && ['pending','uncertain','accepted','rejected'].includes(saved.status)) {
      const receipt = localStorage.getItem(`${prefix}receipt:${saved.id}`);
      const observed = receipt ? reconcileAttempt(saved, JSON.parse(receipt)) : saved;
      const previous = records.get(owner);
      const next = previous?.id === observed.id ? reconcileAttempt(previous, observed) : observed;
      if (JSON.stringify(previous) !== JSON.stringify(next)) records.set(owner, next);
    }
  } catch { failures.add(owner); }
  return records.get(owner);
}
function write(owner: string, value: OperationAttempt) {
  records.set(owner, value);
  try { localStorage.setItem(prefix + owner, JSON.stringify(value)); }
  catch { failures.add(owner); }
  notify();
}
function subscribe(listener: () => void) {
  listeners.add(listener);
  const observe = (event: StorageEvent) => { if (event.key?.startsWith(prefix)) listener(); };
  window.addEventListener('storage', observe);
  return () => { listeners.delete(listener); window.removeEventListener('storage', observe); };
}
/** Storage observations only update evidence. They never issue requests. */
export function useOperation<P>(owner: string, kind: string) {
  const current = useSyncExternalStore(subscribe, useCallback(() => read(owner), [owner])) as OperationAttempt<P> | undefined;
  function prepare(request: P, draftVersion = 0, legacyId?: string) {
    const attempt = prepareAttempt(read(owner) as OperationAttempt<P> | undefined, { owner, kind, request, draftVersion, id: legacyId });
    write(owner, attempt); return attempt;
  }
  function settle(attempt: OperationAttempt<P>, status: OperationAttempt['status'], result?: Record<string, string>, warning?: string) {
    const active = read(owner);
    const receipt = settleAttempt(active?.id === attempt.id ? active as OperationAttempt<P> : attempt, attempt.id, status, result, warning);
    // A's receipt is retained, but A cannot overwrite B's active attempt.
    if (status === 'accepted' || status === 'rejected') {
      const compact = { ...receipt, request: null, fingerprint: '' };
      try { localStorage.setItem(`${prefix}receipt:${attempt.id}`, JSON.stringify(compact)); } catch { failures.add(owner); }
    }
    if (!active || active.id === attempt.id) write(owner, receipt);
    return receipt;
  }
  return { current, prepare, settle, durable: !failures.has(owner), unresolved: current?.status === 'pending' || current?.status === 'uncertain' };
}
