import { Link } from 'react-router';
import { useEffect, useRef, useState } from 'react';
import { clearSubmissionIdentity, get, post, submissionIdentity } from '../api';
import { Button } from './ui/Button';
import type { RecoveryIncident } from '../types';

function RetryRecovery({ endpoint, refresh }: { endpoint: string; refresh?: () => void }) {
  const lock = useRef(false);
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const [error, setError] = useState('');
  async function retry() {
    if (lock.current || accepted) return;
    lock.current = true; setBusy(true); setError('');
    const storage = `recovery:${endpoint}`;
    try {
      await post(endpoint, { request_id: submissionIdentity(storage, { endpoint }) });
      clearSubmissionIdentity(storage); setAccepted(true); refresh?.();
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { lock.current = false; setBusy(false); }
  }
  return <div>{accepted ? <p role="status">Recovery accepted. Follow the task or merge status for the result.</p> :
    <Button disabled={busy} onClick={() => void retry()}>{busy ? 'Requesting recovery…' : 'Retry recovery'}</Button>}
    {error && <p role="alert" className="error">{error}</p>}</div>;
}

export function RecoveryHistory({ incidents, retryBase, historyUrl, workspace = false, refresh }: {
  incidents?: RecoveryIncident[]; retryBase?: string; historyUrl?: string; workspace?: boolean; refresh?: () => void;
}) {
  const [older, setOlder] = useState<RecoveryIncident[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const lock = useRef(false);
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
    try {
      const separator = historyUrl.includes('?') ? '&' : '?';
      const page = await get<{ recoveries?: RecoveryIncident[]; operation?: { recoveries?: RecoveryIncident[] } }>(
        `${historyUrl}${separator}before=${encodeURIComponent(visible[visible.length - 1].incident_id)}&limit=20`);
      const rows = page.recoveries ?? page.operation?.recoveries ?? [];
      setOlder(previous => [...previous, ...rows.filter(row => !previous.some(old => old.incident_id === row.incident_id))]);
      setExhausted(rows.length < 20);
    } catch (reason) { setError(reason instanceof Error ? reason.message : String(reason)); }
    finally { lock.current = false; setBusy(false); }
  }
  if (!incidents?.length) return null;
  return <section className="execution-details" aria-label="Workflow assistant">
    <h2>Workflow assistant</h2>
    {visible.map(incident => <details key={incident.incident_id} open={incident.status === 'working' || incident.status === 'needs_input'}>
      <summary>{incident.issue} · {incident.status === 'working' ? 'Assistant working' : incident.status === 'needs_input' ? 'Needs your input' : incident.status === 'recovered' ? 'Recovered' : incident.status === 'repair_applied' ? 'Review pending' : incident.status === 'replanning' ? 'Refreshing plan' : incident.status === 'awaiting_approval' ? 'Plan ready for approval' : incident.status === 'superseded' ? 'New recovery cycle started' : 'Assessment complete'}</summary>
      <p>{incident.result.summary}</p><p>Next step: {incident.result.next_action}</p>
      <small>{incident.step_id} · {incident.attempts} attempt(s) · <time dateTime={incident.created_at}>{incident.created_at}</time></small>
      {incident.agent_id && <p><Link to={`/agents/${encodeURIComponent(incident.agent_id)}`}>View agent history</Link></p>}
      {retryBase && incident.status === 'needs_input' && (!!incident.operation_id === workspace) &&
        <RetryRecovery endpoint={`${retryBase}/${encodeURIComponent(incident.incident_id)}/retry`} refresh={refresh} />}
      {incident.attempt_history?.map(attempt => <details key={attempt.attempt}>
        <summary>Attempt {attempt.attempt}: {attempt.action} · {attempt.status}</summary>
        {attempt.agent_id && <p><Link to={`/agents/${encodeURIComponent(attempt.agent_id)}`}>View this attempt’s agent</Link></p>}
        <pre style={{ whiteSpace: 'pre-wrap', overflowWrap: 'anywhere' }}>{attempt.report}</pre>
      </details>)}
    </details>)}
    {historyUrl && !exhausted && incidents.length >= 20 && <Button disabled={busy} onClick={() => void loadOlder()}>{busy ? 'Loading history…' : 'Older recovery history'}</Button>}
    {error && <p role="alert" className="error">{error}</p>}
  </section>;
}
