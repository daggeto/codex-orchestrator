import { useOperation } from '../useOperation';
import { Disclosure } from './ui/Disclosure';
import { Link } from 'react-router';
import { useEffect, useRef, useState } from 'react';
import { clearSubmissionIdentity, get, post, submissionIdentity } from '../api';
import { Button } from './ui/Button';
import type { RecoveryIncident } from '../types';

function RetryRecovery({ endpoint, eligible, refresh }: { endpoint: string; eligible: boolean; refresh?: () => void }) {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const operation = useOperation<{ endpoint: string }>(`recovery:${endpoint}`, 'recovery-retry');
  const accepted = operation.current?.status === 'accepted';
  const [error, setError] = useState('');
  async function retry() {
    if (lock.current || accepted || !eligible && !operation.unresolved) return;
    lock.current = true; setBusy(true); setError('');
    const storage = `recovery:${endpoint}`;
    let attempt;
    try {
      attempt = operation.prepare({ endpoint }, 0, submissionIdentity(storage, { endpoint }));
      const result = await post<{ request_id: string; task_id?: string; operation_id?: string }>(endpoint, { request_id: attempt.id });
      operation.settle(attempt, 'accepted', { request_id: result.request_id, ...(result.operation_id ? { operation_id: result.operation_id } : {}) });
      clearSubmissionIdentity(storage, attempt.id); refresh?.();
    } catch (reason) { if (attempt) operation.settle(attempt, 'uncertain', undefined, String(reason)); if (attempt) return; setError(`${String(reason)}. Acceptance is unconfirmed. Check the same request to recover it.`); }
    finally { lock.current = false; setBusy(false); }
  }
  if (!eligible && !operation.current) return null;
  return <div>{accepted ? <p role="status">Recovery accepted. Follow the task or merge status for the result.</p> :
    <Button disabled={busy || !eligible && !operation.unresolved} onClick={() => void retry()}>{busy ? 'Requesting recovery…' : operation.unresolved ? 'Check saved recovery request' : 'Retry recovery'}</Button>}
    {!operation.durable && <p role="alert">Request identity is held only in memory. Keep this page open.</p>}
    {operation.unresolved && operation.current?.warning && <p role="alert" className="error">{operation.current.warning}. Acceptance is unconfirmed. Check the same saved request.</p>}{error && !accepted && <p role="alert" className="error">{error}</p>}</div>;
}

export function RecoveryHistory(props: {
  incidents?: RecoveryIncident[]; retryBase?: string; historyUrl?: string; workspace?: boolean; refresh?: () => void;
}) {
  return <RecoveryHistoryContent key={props.historyUrl || props.retryBase || 'history'} {...props} />;
}
function RecoveryHistoryContent({ incidents, retryBase, historyUrl, workspace = false, refresh }: {
  incidents?: RecoveryIncident[]; retryBase?: string; historyUrl?: string; workspace?: boolean; refresh?: () => void;
}) {
  const [older, setOlder] = useState<RecoveryIncident[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => { controller.current?.abort(); }, []);
  useEffect(() => {
    // Keep rows that move out of the live first page while older pages are open.
    if (incidents?.length) setOlder(previous => {
      const rows = new Map(previous.map(row => [row.incident_id, row]));
      incidents.forEach(row => rows.set(row.incident_id, row));
      return [...rows.values()];
    });
  }, [incidents]);
  const seen = new Set((incidents ?? []).map(incident => incident.incident_id));
  const visible = [...(incidents ?? []), ...older.filter(incident => !seen.has(incident.incident_id))]
    .sort((a, b) => a.created_at === b.created_at ? b.incident_id.localeCompare(a.incident_id) : b.created_at.localeCompare(a.created_at));
  async function loadOlder() {
    if (lock.current || !historyUrl || !visible.length) return;
    lock.current = true; setBusy(true); setError('');
    const request = new AbortController(); controller.current = request;
    try {
      const separator = historyUrl.includes('?') ? '&' : '?';
      const page = await get<{ recoveries?: RecoveryIncident[]; operation?: { recoveries?: RecoveryIncident[] } }>(
        `${historyUrl}${separator}before=${encodeURIComponent(visible[visible.length - 1].incident_id)}&limit=20`, request.signal);
      if (request.signal.aborted) return;
      const rows = page.recoveries ?? page.operation?.recoveries ?? [];
      setOlder(previous => [...previous, ...rows.filter(row => !previous.some(old => old.incident_id === row.incident_id))]);
      setExhausted(rows.length < 20);
    } catch (reason) { if (!request.signal.aborted) setError(String(reason)); }
    finally { if (!request.signal.aborted) { lock.current = false; setBusy(false); } }
  }
  if (!visible.length) return null;
  return <section className="execution-details" aria-label="Workflow assistant">
    <h2>Workflow assistant</h2>
    {visible.map(incident => <Disclosure key={incident.incident_id} defaultOpen={['working', 'needs_input', 'awaiting_approval'].includes(incident.status)} title={<>{incident.issue} · {incident.status === 'working' ? 'Assistant working' : incident.status === 'needs_input' ? 'Needs your input' : incident.status === 'recovered' ? 'Recovered' : incident.status === 'repair_applied' ? 'Review pending' : incident.status === 'replanning' ? 'Refreshing plan' : incident.status === 'awaiting_approval' ? 'Plan ready for approval' : incident.status === 'superseded' ? 'New recovery cycle started' : 'Assessment complete'}</>}>
      <p>{incident.result.summary}</p><p>Next step: {incident.result.next_action}</p>
      <small>{incident.step_id} · {incident.attempts} attempt(s) · <time dateTime={incident.created_at}>{incident.created_at}</time></small>
      {incident.agent_id && <p><Link to={`/agents/${encodeURIComponent(incident.agent_id)}`}>View agent history</Link></p>}
      {(retryBase || !workspace && historyUrl) && (!!incident.operation_id === workspace) &&
        <RetryRecovery key={incident.incident_id} eligible={!!retryBase && incident.status === 'needs_input'} endpoint={`${retryBase || historyUrl}/${encodeURIComponent(incident.incident_id)}/retry`} refresh={refresh} />}
      {incident.attempt_history?.map(attempt => <details key={attempt.attempt}>
        <summary>Attempt {attempt.attempt}: {attempt.action} · {attempt.status}</summary>
        {attempt.agent_id && <p><Link to={`/agents/${encodeURIComponent(attempt.agent_id)}`}>View this attempt’s agent</Link></p>}
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{attempt.report}</pre>
      </details>)}
    </Disclosure>)}
    {historyUrl && !exhausted && (incidents?.length || older.length) >= 20 && <Button disabled={busy} onClick={() => void loadOlder()}>{busy ? 'Loading history…' : 'Older recovery history'}</Button>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
