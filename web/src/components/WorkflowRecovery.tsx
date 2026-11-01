import { useEffect, useRef, useState } from 'react';
import { get, post } from '../api';
import type { JsonObject, WorkflowDefinition, WorkflowRun } from '../types';
import { Button } from './ui/Button';

type RunIdentity = Pick<WorkflowRun, 'run_id' | 'status' | 'updated_at'>;

export function ContinueRun({ run, disabled }: { run: RunIdentity; disabled: boolean }) {
  const [open, setOpen] = useState(run.status === 'paused');
  const [checkpoint, setCheckpoint] = useState<{
    available: boolean; reason: string | null; step: string | null;
    requires_extension?: boolean; iteration_limit?: number; next_iteration_limit?: number;
  }>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState(false);
  const lock = useRef(false);
  useEffect(() => {
    if (!open) return;
    setCheckpoint(undefined); setError('');
    const controller = new AbortController();
    get<{ continuation: NonNullable<typeof checkpoint> }>(`/api/workflows/runs/${encodeURIComponent(run.run_id)}`, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        if (typeof result.continuation?.available !== 'boolean') {
          throw new Error('The running API does not provide checkpoint information. Restart the orchestrator service, then reopen Continue workflow.');
        }
        setCheckpoint(result.continuation); setError('');
      })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [open, run.run_id]);
  const continueRun = async () => {
    if (lock.current || disabled || !checkpoint?.available) return;
    lock.current = true; setBusy(true); setError('');
    const key = `orchestrator.continue.v1:${run.run_id}:${run.updated_at}`;
    try {
      const token = localStorage.getItem(key) || crypto.randomUUID();
      localStorage.setItem(key, token);
      await post(`/api/workflows/runs/${encodeURIComponent(run.run_id)}/continue`, {
        request_id: token, additional_steps: checkpoint.requires_extension ? 20 : 0,
      });
      setAccepted(true);
      localStorage.removeItem(key);
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : 'Continuation failed'}. Retrying uses the same request ID.`);
    } finally { lock.current = false; setBusy(false); }
  };
  return <details className="restart-run" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Continue workflow</summary>
    <p>Retries the unfinished step using saved outputs, workspace, and conversation when available. Completed workflow steps are skipped. The unfinished step may repeat actions it already started.</p>
    <p>Restore your allowance before continuing after a usage limit. Publication can proceed if it was enabled for this run.</p>
    {!checkpoint && !error && <p role="status">Loading checkpoint…</p>}
    {checkpoint?.step && <p>Continue from: <code>{checkpoint.step}</code></p>}
    {checkpoint?.requires_extension && <p role="status">The {checkpoint.iteration_limit}-step budget is used up. Allow another 20 steps? The new limit will be {checkpoint.next_iteration_limit}.</p>}
    {checkpoint && !checkpoint.available && <p>{checkpoint.reason}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {accepted ? <p role="status">Continuation accepted. This run will update as it progresses.</p> :
      <Button disabled={disabled || busy || !checkpoint?.available} onClick={() => { void continueRun(); }}>
        {busy ? 'Submitting…' : checkpoint?.requires_extension ? 'Allow 20 more steps and continue' : 'Continue from saved step'}
      </Button>}
  </details>;
}

export function RestartRun({ run, disabled, select }: { run: RunIdentity; disabled: boolean; select: (id: string, taskId?: string) => void }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<WorkflowRun>();
  const [definition, setDefinition] = useState<WorkflowDefinition>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState<{ run_id: string; task_id?: string }>();
  const lock = useRef(false);
  const storageKey = `orchestrator.restart.v1.${run.run_id}`;
  useEffect(() => {
    if (!open) return;
    setSource(undefined); setDefinition(undefined); setError('');
    const controller = new AbortController();
    Promise.all([
      get<{ run: WorkflowRun }>(`/api/workflows/runs/${encodeURIComponent(run.run_id)}`, controller.signal),
      get<{ workflows: WorkflowDefinition[] }>('/api/workflows', controller.signal),
    ]).then(([saved, metadata]) => {
      if (controller.signal.aborted) return;
      setSource(saved.run);
      setDefinition(metadata.workflows.find(item => item.name === saved.run.workflow_name));
      setError('');
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [open, run.run_id]);
  const variables = source?.config.variables as JsonObject | undefined;
  const valid = variables && typeof variables.cwd === 'string' && typeof variables.task_description === 'string';
  const stopped = source && ['interrupted', 'failed', 'cancelled', 'paused'].includes(source.status);
  const restart = async () => {
    if (lock.current || disabled || !source || !valid || !stopped || !definition) return;
    lock.current = true; setBusy(true); setError('');
    try {
      // Keep the exact request across timeouts, navigation, and reloads. Reusing
      // its ID recovers acceptance without scheduling duplicate executions.
      const stored = localStorage.getItem(storageKey);
      const request = stored ? JSON.parse(stored) : {
        run_id: crypto.randomUUID(), workflow: source.workflow_name, variables,
      };
      if (typeof request.run_id !== 'string' || request.workflow !== source.workflow_name ||
          !request.variables || typeof request.variables !== 'object') throw new Error('Saved restart request is invalid.');
      localStorage.setItem(storageKey, JSON.stringify(request));
      const result = await post<{ run_id: string; task_id?: string }>(`/api/workflows/${encodeURIComponent(request.workflow)}/submit`, {
        run_id: request.run_id, variables: request.variables,
      });
      setAccepted(result);
    } catch (reason) {
      setError(`${reason instanceof Error ? reason.message : 'Restart failed'}. Retry uses the same request ID.`);
    } finally { lock.current = false; setBusy(false); }
  };
  return <details className="restart-run" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Restart run</summary>
    <p>Starts a new run from the beginning using the saved inputs. Completed steps may run again. The previous run and its files are retained; this does not continue its saved progress.</p>
    {open && !source && !error && <p role="status">Loading saved inputs…</p>}
    {source && <>
      <p>Project: <code>{valid ? variables.cwd as string : 'Unavailable'}</code></p>
      {valid && <p className="restart-task">{variables.task_description as string}</p>}
      <p>{!definition ? 'Workflow definition unavailable.' : definition.publication_policy === 'required' ||
        (definition.publication_policy === 'optional' && !['false', '0', 'no'].includes(String(variables?.create_mr ?? false).toLowerCase()))
        ? 'This run may push commits and publish a PR/MR.' : 'PR/MR publication is disabled for this run.'}</p>
      {!valid && <p className="error">Saved project or task inputs are missing. Start a task from Task home.</p>}
      {!stopped && <p>This run is no longer stopped. Refresh the page before restarting.</p>}
      <details><summary>Saved workflow inputs</summary><pre>{JSON.stringify(variables, null, 2)}</pre></details>
    </>}
    {error && <p className="error" role="alert">{error}</p>}
    {accepted ? <p role="status">New run accepted: <Button onClick={() => select(accepted.run_id, accepted.task_id)}>View run {accepted.run_id.slice(0, 12)}</Button></p>
      : <Button disabled={disabled || busy || !valid || !stopped || !definition} onClick={() => { void restart(); }}>
        {busy ? 'Submitting…' : 'Start new run with these inputs'}
      </Button>}
  </details>;
}

