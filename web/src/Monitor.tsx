import { useEffect, useRef, useState } from 'react';
import { agentStatus, ApiError, branchDiff, get, post, taskDetail, taskRelationships } from './api';
import type { JsonObject, MonitorEvent, TaskDetail, WorkflowDefinition, WorkflowRun, Workspace } from './types';
import { useMonitor } from './useMonitor';

function useResource<T>(key: string | null, load: (key: string, signal: AbortSignal) => Promise<T>, interval: number) {
  const [result, setResult] = useState<{ key: string | null; value?: T; error?: string; missing?: boolean; stale?: boolean }>({ key: null });
  useEffect(() => {
    if (!key) { setResult({ key: null }); return; }
    const controller = new AbortController();
    let busy = false;
    setResult({ key });
    const refresh = async () => {
      if (busy) return;
      busy = true;
      try {
        const value = await load(key, controller.signal);
        if (!controller.signal.aborted) setResult({ key, value });
      } catch (error) {
        if (!controller.signal.aborted) setResult(previous => ({
          key, value: previous.key === key ? previous.value : undefined, stale: true,
          missing: error instanceof ApiError && error.status === 404,
          error: error instanceof Error ? error.message : 'Request failed',
        }));
      } finally { busy = false; }
    };
    void refresh();
    const timer = setInterval(() => { void refresh(); }, interval);
    return () => { controller.abort(); clearInterval(timer); };
  }, [key, load, interval]);
  return result.key === key ? result : { key };
}
const loadTask = async (id: string, signal: AbortSignal) => (await taskDetail(id, signal)).item;
const loadDiff = async (id: string, signal: AbortSignal) => (await branchDiff(id, signal)).diff;
const json = (value: unknown) => JSON.stringify(value, null, 2);
const active = (status: string) => !['completed', 'failed', 'cancelled', 'interrupted', 'paused', 'done'].includes(status);
const connectionLabels = {
  connecting: 'Connecting', connected: 'Connected', disconnected: 'Disconnected',
  reconnecting: 'API reachable, live stream reconnecting',
};

function ContinueRun({ run, disabled }: { run: WorkflowRun; disabled: boolean }) {
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
      <button disabled={disabled || busy || !checkpoint?.available} onClick={() => { void continueRun(); }}>
        {busy ? 'Submitting…' : checkpoint?.requires_extension ? 'Allow 20 more steps and continue' : 'Continue from saved step'}
      </button>}
  </details>;
}

function RestartRun({ run, disabled, select }: { run: WorkflowRun; disabled: boolean; select: (id: string) => void }) {
  const [open, setOpen] = useState(false);
  const [source, setSource] = useState<WorkflowRun>();
  const [definition, setDefinition] = useState<WorkflowDefinition>();
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);
  const [acceptedId, setAcceptedId] = useState('');
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
      const result = await post<{ run_id: string }>(`/api/workflows/${encodeURIComponent(request.workflow)}/submit`, {
        run_id: request.run_id, variables: request.variables,
      });
      setAcceptedId(result.run_id);
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
      {!stopped && <p>This run is no longer stopped. Refresh the monitor before restarting.</p>}
      <details><summary>Saved workflow inputs</summary><pre>{json(variables)}</pre></details>
    </>}
    {error && <p className="error" role="alert">{error}</p>}
    {acceptedId ? <p role="status">New run accepted: <button onClick={() => select(acceptedId)}>View run {acceptedId.slice(0, 12)}</button></p>
      : <button disabled={disabled || busy || !valid || !stopped || !definition} onClick={() => { void restart(); }}>
        {busy ? 'Submitting…' : 'Start new run with these inputs'}
      </button>}
  </details>;
}

function Events({ events, gap }: { events: MonitorEvent[]; gap: boolean }) {
  const [follow, setFollow] = useState(true);
  const panel = useRef<HTMLDivElement>(null);
  useEffect(() => { if (follow && panel.current) panel.current.scrollTop = panel.current.scrollHeight; }, [events, follow]);
  return <section>
    <div className="section-title"><h2>Live events</h2><label><input type="checkbox" checked={follow} onChange={e => setFollow(e.target.checked)} /> Follow</label></div>
    <p className="muted">Latest 1,000 events. Events have no replay and may be dropped. {gap && 'Connection interrupted; logs may have a gap.'}</p>
    <div className="events" ref={panel} tabIndex={0} aria-label="Live event stream">
      {!events.length && <p className="muted">Waiting for events from this instance.</p>}
      {events.map((event, index) => <article key={index}>
        <div><time>{event.timestamp}</time> <strong>{event.topic}</strong> <span className="muted">{event.source}</span></div>
        <pre>{json(event.payload)}</pre>
      </article>)}
    </div>
  </section>;
}
function TaskInfo({ task, workspaces, chooseWorkspace }: {
  task: TaskDetail; workspaces: Workspace[]; chooseWorkspace: (id: string) => void;
}) {
  const relationships = taskRelationships(task, workspaces);
  return <>
    <h3>{task.title}</h3><p>{task.description || 'No description recorded.'}</p>
    <p>{task.status} · Agent: {relationships.agentId || 'Unassigned'} · Thread: {task.assigned_thread || 'Unavailable'}</p>
    <p className="muted">Created {task.created_at} · Updated {task.updated_at}</p>
    {relationships.workspaceIds.map(id => <button key={id} onClick={() => chooseWorkspace(id)}>
      Workspace: {workspaces.find(w => w.worktree_id === id)?.branch || id}
    </button>)}
    <details><summary>Input, output, and assignment history</summary><pre>{json({ input: task.input_data, output: task.output_data, history: task.history })}</pre></details>
  </>;
}
export function Monitor() {
  const monitor = useMonitor();
  const { data, selection, select } = monitor;
  const task = useResource(selection?.kind === 'task' ? selection.id : null, loadTask, 2000);
  const selectedAgent = data.agents.find(a => selection?.kind === 'agent' && a.agent_id === selection.id);
  const selectedRun = data.runs.find(r => selection?.kind === 'run' && r.run_id === selection.id);
  const workspaceId = selection?.kind === 'workspace' ? selection.id :
    (task.value ? taskRelationships(task.value, data.workspaces).workspaceIds[0] : undefined);
  const workspace = data.workspaces.find(w => w.worktree_id === workspaceId);
  const diff = useResource(workspaceId || null, loadDiff, 5000);
  const tasks = [...data.tasks].sort((a, b) => Number(active(b.status)) - Number(active(a.status)) || b.updated_at.localeCompare(a.updated_at));
  const runs = [...data.runs].sort((a, b) => Number(active(b.status)) - Number(active(a.status)) || b.updated_at.localeCompare(a.updated_at));
  const selected = (kind: string, id: string) => selection?.kind === kind && selection.id === id;
  return <>
    <header><h1>Orchestrator monitor</h1><div role="status" className={`connection ${monitor.connection}`}>
      {connectionLabels[monitor.connection]}{monitor.stale && ' · Data stale'}
    </div><p className="muted">127.0.0.1:8420{monitor.updatedAt && ` · Snapshot ${monitor.updatedAt}`}</p></header>
    {monitor.error && <p className="error" role="alert">{monitor.error}. Previous data is retained. Check that the orchestrator API is running.</p>}
    <main>
      <aside>
        <section><h2>Agents ({data.agents.length})</h2>
          {!data.agents.length && <p className="muted">No agents reported.</p>}
          {data.agents.map(agent => {
            const currentTasks = data.tasks.filter(t => active(t.status) && (t.assigned_agent === agent.agent_id ||
              taskRelationships(t, data.workspaces).agentId === agent.agent_id));
            return <button className={`row ${selected('agent', agent.agent_id) ? 'selected' : ''}`} key={agent.agent_id} onClick={() => select({ kind: 'agent', id: agent.agent_id })}>
              <strong>{agent.name || agent.role}</strong><span>{agentStatus(agent)} · {agent.backend}</span>
              {currentTasks.map(t => <span key={t.item_id}>Task: {t.title}</span>)}
              {agent.activity?.threads.map(thread => <span key={thread.thread_id}>{thread.message_preview}<br />Workspace: {thread.workspace || 'Unavailable'}</span>)}
              {agent.activity?.error && <span className="error">{agent.activity.error}</span>}
            </button>;
          })}
        </section>
        <section><h2>Tasks ({tasks.length})</h2>{!tasks.length && <p className="muted">No tasks recorded.</p>}
          {tasks.map(item => <button className={`row ${selected('task', item.item_id) ? 'selected' : ''}`} key={item.item_id} onClick={() => select({ kind: 'task', id: item.item_id })}>
            <strong>{item.title}</strong><span>{item.status} · Priority {item.priority}</span><small>{item.updated_at}</small>
          </button>)}
        </section>
        <section><h2>Workflow runs ({runs.length})</h2>{!runs.length && <p className="muted">No workflow runs recorded.</p>}
          {runs.map(run => <div className="workflow-run" key={run.run_id}><button className={`row ${selected('run', run.run_id) ? 'selected' : ''}`} onClick={() => select({ kind: 'run', id: run.run_id })}>
            <strong>{run.workflow_name}</strong><span>{run.status} · {run.run_id.slice(0, 12)}</span><small>{run.updated_at}</small>
          </button>
            {['interrupted', 'failed', 'cancelled', 'paused'].includes(run.status) && <>
              <ContinueRun key={`${run.run_id}:${run.updated_at}`} run={run} disabled={monitor.stale} />
              <RestartRun run={run} disabled={monitor.stale} select={id => select({ kind: 'run', id })} />
            </>}
          </div>)}
        </section>
        <section><h2>Workspaces ({data.workspaces.length})</h2>{!data.workspaces.length && <p className="muted">No registered workspaces.</p>}
          {data.workspaces.map(w => <button className={`row ${selected('workspace', w.worktree_id) ? 'selected' : ''}`} key={w.worktree_id} onClick={() => select({ kind: 'workspace', id: w.worktree_id })}>
            <strong>{w.branch}</strong><span>{w.status}</span><small>{w.path}</small>
          </button>)}
        </section>
      </aside>
      <div className="right">
        <section><h2>Selected details</h2>
          {!selection && <p className="muted">Select an agent, task, run, or workspace.</p>}
          {selection?.kind === 'task' && <>
            {!task.value && !task.error && <p>Loading task…</p>}
            {task.error && <p className="error">{task.missing ? 'Task is unavailable.' : task.error}{task.value && ' · Details stale'}</p>}
            {task.value && <TaskInfo task={task.value} workspaces={data.workspaces} chooseWorkspace={id => select({ kind: 'workspace', id })} />}
          </>}
          {selection?.kind === 'agent' && (selectedAgent ? <>
            <h3>{selectedAgent.name}</h3><pre>{json(selectedAgent)}</pre>
            {data.tasks.filter(t => taskRelationships(t, data.workspaces).agentId === selectedAgent.agent_id).map(t =>
              <button key={t.item_id} onClick={() => select({ kind: 'task', id: t.item_id })}>{t.title}</button>)}
            {data.workspaces.filter(w => w.agent_id === selectedAgent.agent_id).map(w =>
              <button key={w.worktree_id} onClick={() => select({ kind: 'workspace', id: w.worktree_id })}>{w.branch}</button>)}
          </> : <p>Agent no longer available.</p>)}
          {selection?.kind === 'run' && (selectedRun ? <><h3>{selectedRun.workflow_name}</h3><pre>{json(selectedRun)}</pre></> : <p>Run no longer in recent history.</p>)}
          {selection?.kind === 'workspace' && (workspace ? <><pre>{json(workspace)}</pre>
            {workspace.work_item_id && <button onClick={() => select({ kind: 'task', id: workspace.work_item_id! })}>Open task</button>}
          </> : <p>Workspace registration unavailable. Historical workspaces may have been removed.</p>)}
        </section>
        <Events events={monitor.events} gap={monitor.gap} />
        <section><h2>Branch diff{workspace && ` · ${workspace.branch}`}</h2>
          <p className="muted">Committed branch changes against the base branch. Excludes uncommitted and untracked files. Refreshes every five seconds.</p>
          {!workspaceId && <p className="muted">Select a workspace or a task with a recorded workspace.</p>}
          {workspaceId && !diff.value && diff.value !== '' && !diff.error && <p>Loading branch diff…</p>}
          {diff.error && <p className="error">{diff.missing ? 'Workspace unavailable; its branch diff cannot be retrieved.' : `Diff request failed: ${diff.error}`}{diff.value !== undefined && ' · Previous patch is stale'}</p>}
          {diff.value === '' && !diff.missing && <p>No committed branch changes.</p>}
          {diff.value && <pre className="diff">{diff.value}</pre>}
        </section>
      </div>
    </main>
  </>;
}
