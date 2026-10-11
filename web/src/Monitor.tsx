import { useEffect, useRef, useState } from 'react';
import { agentStatus, ApiError, branchDiff, taskDetail, taskRelationships } from './api';
import type { MonitorEvent, TaskDetail, Workspace } from './types';
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
const active = (status: string) => !['completed', 'failed', 'cancelled', 'done'].includes(status);
const connectionLabels = {
  connecting: 'Connecting', connected: 'Connected', disconnected: 'Disconnected',
  reconnecting: 'API reachable, live stream reconnecting',
};

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
          {runs.map(run => <button className={`row ${selected('run', run.run_id) ? 'selected' : ''}`} key={run.run_id} onClick={() => select({ kind: 'run', id: run.run_id })}>
            <strong>{run.workflow_name}</strong><span>{run.status} · {run.run_id.slice(0, 12)}</span><small>{run.updated_at}</small>
          </button>)}
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
