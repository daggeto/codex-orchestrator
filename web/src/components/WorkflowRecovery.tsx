import { useOperation } from '../useOperation';
import { Checkbox } from './ui/primitives/checkbox';
import { useEffect, useRef, useState } from 'react';
import { ApiError, get, post } from '../api';
import type { Attachment, JsonObject, WorkflowDefinition, WorkflowRun } from '../types';
import { Button } from './ui/Button';

type RunIdentity = Pick<WorkflowRun, 'run_id' | 'status' | 'updated_at'>;

function object(value: unknown): JsonObject {
  return value !== null && typeof value === 'object' && !Array.isArray(value) ? value as JsonObject : {};
}

export function ValidationRecovery({ state }: { state?: JsonObject }) {
  const checkpoint = object(state?.checkpoint);
  const variables = object(state?.final_variables ?? checkpoint.variables ?? state?.variables);
  const stop = object(variables.validation_stop);
  let checks = stop.checks;
  // Saved runs from before structured recovery still have useful QA evidence.
  if (!Array.isArray(checks) && typeof variables.validation_report === 'string') {
    try { checks = object(JSON.parse(variables.validation_report)).checks; } catch { /* Show the saved error below. */ }
  }
  const issues = Array.isArray(checks) ? checks.map(object).filter(check => ['failed', 'blocked'].includes(String(check.status))) : [];
  const error = typeof state?.error === 'string' ? state.error : '';
  if (!Object.keys(stop).length && !error.includes('validate')) return null;
  const actions = Array.isArray(stop.actions) ? stop.actions.filter((action): action is string => typeof action === 'string') : [];
  return <section className="execution-details" aria-label="Validation recovery">
    <h2>Validation needs attention</h2>
    <p>{typeof stop.reason === 'string' ? stop.reason : error}</p>
    {typeof stop.repairs_used === 'number' && typeof stop.repair_limit === 'number' && <p>Repair rounds used: {stop.repairs_used} of {stop.repair_limit}.</p>}
    {issues.length > 0 && <ul>{issues.map((check, index) => <li key={index}>
      <strong>{check.status === 'blocked' ? 'Environment blocker' : 'Check failed'}</strong>
      {typeof check.command === 'string' && check.command && <>: <code>{check.command}</code></>}
      {typeof check.reason === 'string' && <p>{check.reason}</p>}
      {typeof check.action === 'string' && <p>Next step: {check.action}</p>}
    </li>)}</ul>}
    <h3>How to continue</h3>
    {actions.length ? <ol>{actions.map((action, index) => <li key={index}>{action}</li>)}</ol> :
      <p>Resolve the reported environment blockers, then choose Continue. Repairable test failures will be sent to the implementer.</p>}
    <p>Continue authorizes fresh QA and up to three repair rounds. If the same failures persist, the workflow stops again for your review.</p>
  </section>;
}

export function ContinueRun({ run, disabled }: { run: RunIdentity; disabled: boolean }) {
  const [open, setOpen] = useState(run.status === 'paused');
  const [checkpoint, setCheckpoint] = useState<{
    available: boolean; reason: string | null; step: string | null;
    requires_extension?: boolean; iteration_limit?: number; next_iteration_limit?: number;
    review_refresh_required?: boolean;
  }>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const operation = useOperation<{ additional_steps: 0 | 20 }>(`continue:${run.run_id}`, 'continue');
  const accepted = operation.current?.status === 'accepted' && operation.current.result?.checkpoint === run.updated_at;
  const lock = useRef(false);
  useEffect(() => {
    if (!open) return;
    setCheckpoint(undefined); setError('');
    const controller = new AbortController();
    get<{ continuation: NonNullable<typeof checkpoint>; run: WorkflowRun }>(`/api/workflows/runs/${encodeURIComponent(run.run_id)}`, controller.signal)
      .then(result => {
        if (controller.signal.aborted) return;
        if (typeof result.continuation?.available !== 'boolean') {
          throw new Error('The running API does not provide checkpoint information. Restart the orchestrator service, then reopen Continue workflow.');
        }
        setCheckpoint(result.continuation); setError('');
        const saved = operation.current;
        const attempts = result.run.state?.continuations;
        if (saved && operation.unresolved && Array.isArray(attempts) && attempts.some(item => object(item).request_id === saved.id)) operation.settle(saved, 'accepted', { checkpoint: saved.result?.checkpoint || run.updated_at });
      })
      .catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [open, run.run_id, run.updated_at]);
  const continueRun = async () => {
    if (lock.current || disabled || !operation.unresolved && !checkpoint?.available) return;
    lock.current = true; setBusy(true); setError('');
    let attempt = operation.current;
    try {
      const observation = await get<{ run: WorkflowRun; continuation: NonNullable<typeof checkpoint> }>(`/api/workflows/runs/${encodeURIComponent(run.run_id)}`);
      if (attempt && operation.unresolved && Array.isArray(observation.run.state?.continuations) && observation.run.state.continuations.some(item => object(item).request_id === attempt!.id)) {
        operation.settle(attempt, 'accepted', { checkpoint: attempt.result?.checkpoint || run.updated_at }); return;
      }
      if (!operation.unresolved) {
        if (!observation.continuation.available) throw new Error(observation.continuation.reason || 'Continuation is unavailable');
        if (observation.run.updated_at !== run.updated_at || observation.continuation.requires_extension !== checkpoint?.requires_extension) {
          setCheckpoint(observation.continuation); throw new Error('The checkpoint changed. Review the latest task state before continuing.');
        }
        let legacyId: string | undefined;
        try { legacyId = localStorage.getItem(`orchestrator.continue.v1:${run.run_id}:${run.updated_at}`) || undefined; } catch { /* New identities remain available in memory. */ }
        attempt = operation.prepare({ additional_steps: checkpoint?.requires_extension ? 20 : 0 }, 0, legacyId);
      }
      if (!attempt?.request) return;
      await post(`/api/workflows/runs/${encodeURIComponent(run.run_id)}/continue`, { request_id: attempt.id, ...attempt.request });
      operation.settle(attempt, 'accepted', { checkpoint: run.updated_at });
      try {
        const key = `orchestrator.continue.v1:${run.run_id}:${run.updated_at}`;
        if (localStorage.getItem(key) === attempt.id) localStorage.removeItem(key);
      } catch { /* Receipt remains in memory. */ }
    } catch (reason) {
      if (attempt && attempt.status !== 'accepted') operation.settle(attempt, 'uncertain', undefined, String(reason));
      setError(`${String(reason)}. Acceptance is unconfirmed. Check the saved request before starting another continuation.`);
    } finally { lock.current = false; setBusy(false); }
  };
  return <details className="restart-run" open={open} onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Continue workflow</summary>
    <p>Retries the unfinished step using saved outputs, workspace, and conversation when available. Completed workflow steps are skipped. The unfinished step may repeat actions it already started.</p>
    {checkpoint?.step === 'validate' && <p>Continuing final validation runs fresh automated checks and authorizes up to three repair rounds. Repeated failures stop for your review. Earlier results are retained; resolve environment blockers before continuing.</p>}
    <p>Restore your allowance before continuing after a usage limit. Publication can proceed if it was enabled for this run.</p>
    {!checkpoint && !error && <p role="status">Loading checkpoint…</p>}
    {checkpoint?.step && <p>Continue from: <code>{checkpoint.step}</code></p>}
    {checkpoint?.review_refresh_required && <p>Your workspace and implementation are kept. Review rules changed, so completed work needs fresh reviewer approval before finishing. Continue also authorizes fresh final checks and up to three repair rounds.</p>}
    {checkpoint?.requires_extension && <p role="status">The {checkpoint.iteration_limit}-step budget is used up. Allow another 20 steps? The new limit will be {checkpoint.next_iteration_limit}.</p>}
    {checkpoint && !checkpoint.available && <p>{checkpoint.reason}</p>}
    {error && <p className="error" role="alert">{error}</p>}
    {!operation.durable && <p role="alert">Request identity is held only in memory. Keep this page open.</p>}
    {accepted ? <p role="status">Continuation accepted. This run will update as it progresses.</p> :
      <Button disabled={disabled || busy || !operation.unresolved && !checkpoint?.available} onClick={() => { void continueRun(); }}>
        {busy ? 'Submitting…' : operation.unresolved ? 'Check saved continuation' : checkpoint?.requires_extension ? 'Allow 20 more steps and continue' : 'Continue from saved step'}
      </Button>}
  </details>;
}

export function RestartRun({ run, disabled, select }: { run: RunIdentity; disabled: boolean; select: (id: string, taskId?: string) => void }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<WorkflowRun>();
  const [sourceTaskId, setSourceTaskId] = useState<string>();
  const [destination, setDestination] = useState<{ path: string; kind: string } | null>(null);
  const [attachments, setAttachments] = useState<Attachment[]>([]);
  const [omitUnavailable, setOmitUnavailable] = useState(false);
  const [definition, setDefinition] = useState<WorkflowDefinition>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [accepted, setAccepted] = useState<{ run_id: string; task_id?: string }>();
  const lock = useRef(false);
  const storageKey = `orchestrator.restart.v1.${run.run_id}`;
  const restartOperation = useOperation<Record<string, unknown>>(`restart:${run.run_id}`, 'restart');
  useEffect(() => {
    if (!open) return;
    setSource(undefined); setDefinition(undefined); setError('');
    const controller = new AbortController();
    Promise.all([
      get<{ run: WorkflowRun; task_id?: string; attachments?: Attachment[]; restart_workspace?: { path: string; kind: string } | null }>(`/api/workflows/runs/${encodeURIComponent(run.run_id)}`, controller.signal),
      get<{ workflows: WorkflowDefinition[] }>('/api/workflows', controller.signal),
    ]).then(([saved, metadata]) => {
      if (controller.signal.aborted) return;
      setSource(saved.run); setSourceTaskId(saved.task_id); setAttachments(saved.attachments || []);
      setDestination(saved.restart_workspace || null);
      setDefinition(metadata.workflows.find(item => item.name === saved.run.workflow_name));
      setError('');
    }).catch(reason => { if (!controller.signal.aborted) setError(String(reason)); });
    return () => controller.abort();
  }, [open, run.run_id]);
  useEffect(() => {
    const result = restartOperation.current?.status === 'accepted' ? restartOperation.current.result : undefined;
    if (result?.run_id) setAccepted({ run_id: result.run_id, task_id: result.task_id });
  }, [restartOperation.current]);
  const variables = source?.config.variables as JsonObject | undefined;
  const valid = variables && typeof variables.cwd === 'string' && typeof variables.task_description === 'string';
  const stopped = source && ['interrupted', 'failed', 'cancelled', 'paused'].includes(source.status);
  const destinationAvailable = !sourceTaskId || !!destination;
  const restart = async () => {
    if (lock.current || disabled || !source || !valid || !stopped || !definition || !destinationAvailable) return;
    lock.current = true; setBusy(true); setError('');
    let attempt = restartOperation.current;
    try {
      let legacy: Record<string, unknown> | undefined;
      try { const stored = localStorage.getItem(storageKey); legacy = stored ? JSON.parse(stored) : undefined; } catch { /* Use in-memory recovery. */ }
      const request = attempt && restartOperation.unresolved ? attempt.request : legacy || {
        run_id: crypto.randomUUID(), workflow: source.workflow_name,
        variables: { ...variables, ...(destination ? { cwd: destination.path } : {}) },
        copy_from_task_id: sourceTaskId, omit_unavailable_attachments: omitUnavailable,
      };
      if (!request || typeof request.run_id !== 'string' || request.workflow !== source.workflow_name || !request.variables || typeof request.variables !== 'object') throw new Error('Saved restart request is invalid.');
      attempt = restartOperation.prepare(request, 0, request.run_id);
      // A saved attempt is observed before explicit replay. Reload never submits.
      try {
        const found = await get<{ run: WorkflowRun; task_id?: string }>(`/api/workflows/runs/${encodeURIComponent(request.run_id)}`);
        const result = { run_id: found.run.run_id, ...(found.task_id ? { task_id: found.task_id } : {}) };
        restartOperation.settle(attempt, 'accepted', result); setAccepted(result); return;
      } catch (reason) { if (!(reason instanceof ApiError) || reason.status !== 404) throw reason; }
      const result = await post<{ run_id: string; task_id?: string }>(`/api/workflows/${encodeURIComponent(String(request.workflow))}/submit`, {
        run_id: request.run_id, variables: request.variables, copy_from_task_id: request.copy_from_task_id, omit_unavailable_attachments: request.omit_unavailable_attachments,
      });
      restartOperation.settle(attempt, 'accepted', { run_id: result.run_id, ...(result.task_id ? { task_id: result.task_id } : {}) });
      setAccepted(result);
      try { const old = JSON.parse(localStorage.getItem(storageKey) || 'null'); if (old?.run_id === request.run_id) localStorage.removeItem(storageKey); } catch { /* Receipt is retained in memory. */ }
    } catch (reason) {
      if (attempt) restartOperation.settle(attempt, 'uncertain', undefined, String(reason));
      setError(`${String(reason)}. Your exact saved inputs are retained. Check this request before starting another.`);
    } finally { lock.current = false; setBusy(false); }
  };
  return <details className="restart-run" onToggle={event => setOpen(event.currentTarget.open)}>
    <summary>Restart run</summary>
    <p>Starts a new run from the beginning using the saved inputs. Completed steps may run again. Available task attachments are copied into independent ownership for the new task. Removing either worktree deletes only its own attachments. This does not continue saved progress.</p>
    {open && !source && !error && <p role="status">Loading saved inputs…</p>}
    {source && <>
      <p>Project: <code>{valid ? variables.cwd as string : 'Unavailable'}</code></p>
      {destination && <p>Restart destination: {destination.kind === 'main' ? 'Main workspace' : 'Current worktree'} <code>{destination.path}</code>. Workspace isolation applies to the new task.</p>}
      {!destinationAvailable && <p className="error" role="alert">No active restart workspace is available. Restore the repository Main workspace, then reopen Restart run. Your saved request is retained.</p>}
      {valid && <p className="restart-task">{variables.task_description as string}</p>}
      <p>{!definition ? 'Workflow definition unavailable.' : definition.publication_policy === 'required' ||
        (definition.publication_policy === 'optional' && !['false', '0', 'no'].includes(String(variables?.create_mr ?? false).toLowerCase()))
        ? 'This run may push commits and publish a PR/MR.' : 'PR/MR publication is disabled for this run.'}</p>
      {!valid && <p className="error">Saved project or task inputs are missing. Start a task from Task home.</p>}
      {!stopped && <p>This run is no longer stopped. Refresh the page before restarting.</p>}
      {!!attachments.length && <p>{attachments.filter(file => file.state === 'ready').length} attachments will be copied to the new task.</p>}
      {attachments.some(file => file.state !== 'ready') && <div><p>Unavailable files: {attachments.filter(file => file.state !== 'ready').map(file => file.filename).join(', ')}. Start a task from its workspace to reselect these files, or explicitly omit them here.</p><label><Checkbox checked={omitUnavailable} onCheckedChange={setOmitUnavailable} />Restart without unavailable attachments</label></div>}
      <details><summary>Saved workflow inputs</summary><pre>{JSON.stringify(variables, null, 2)}</pre></details>
    </>}
    {error && <p className="error" role="alert">{error}</p>}
    {!restartOperation.durable && <p role="alert">Restart recovery is held only in memory. Keep this page open.</p>}
    {accepted ? <p role="status">New run accepted: <Button onClick={() => select(accepted.run_id, accepted.task_id)}>View run {accepted.run_id.slice(0, 12)}</Button></p>
      : <Button disabled={disabled || busy || !valid || !stopped || !definition || !destinationAvailable || (attachments.some(file => file.state !== 'ready') && !omitUnavailable)} onClick={() => { void restart(); }}>
        {busy ? 'Submitting…' : restartOperation.unresolved ? 'Check saved restart request' : 'Start new run with these inputs'}
      </Button>}
  </details>;
}

