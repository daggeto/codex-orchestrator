/** Acceptance is a receipt, independent of the mutable task/run outcome. */
export interface OperationAttempt<P = unknown> {
  id: string; owner: string; kind: string; request: P | null; fingerprint: string;
  draftVersion: number; status: 'pending' | 'uncertain' | 'accepted' | 'rejected';
  result?: Record<string, string>; warning?: string;
}
export function prepareAttempt<P>(current: OperationAttempt<P> | undefined, input: {
  owner: string; kind: string; request: P; draftVersion?: number; id?: string;
}): OperationAttempt<P> {
  const fingerprint = JSON.stringify(input.request);
  if (current && (current.owner !== input.owner || current.kind !== input.kind)) throw new Error('Operation identity belongs to another action.');
  if (current && (current.status === 'pending' || current.status === 'uncertain')) {
    if (current.fingerprint !== fingerprint) throw new Error('A saved request still needs reconciliation. Check the saved attempt before submitting changed content.');
    return current;
  }
  return { ...input, id: input.id ?? crypto.randomUUID(), draftVersion: input.draftVersion ?? 0, fingerprint, status: 'pending' };
}
export function reconcileAttempt<P>(current: OperationAttempt<P>, observation: OperationAttempt<P>): OperationAttempt<P> {
  if (current.id !== observation.id || current.owner !== observation.owner) return current;
  if (current.status === 'accepted') return current;
  if (observation.status === 'accepted') return { ...observation, request: null, fingerprint: '', warning: undefined };
  if (current.status === 'rejected') return current;
  if (observation.status === 'rejected') return { ...observation, request: null, fingerprint: '' };
  return observation;
}
export function settleAttempt<P>(current: OperationAttempt<P>, id: string, status: OperationAttempt['status'], result?: Record<string, string>, warning?: string) {
  return reconcileAttempt(current, { ...current, id, status, result, warning });
}
export function mayClearDraft(attempt: OperationAttempt, activeId: string, draftVersion: number) {
  return attempt.id === activeId && attempt.status === 'accepted' && attempt.draftVersion === draftVersion;
}
