import type { Agent, Connection, JsonObject, MonitorEvent, Snapshot, Task, TaskDetail, WorkflowRun, Workspace } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number) { super(message); }
}
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(5000);
  const response = await fetch(path, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
  if (!response.ok) throw new ApiError(`Request failed (${response.status})`, response.status);
  return response.json() as Promise<T>;
}
const object = (value: unknown): value is JsonObject => typeof value === 'object' && value !== null && !Array.isArray(value);
export function parseEvent(data: string): MonitorEvent | null {
  try {
    const value: unknown = JSON.parse(data);
    if (!object(value) || typeof value.topic !== 'string' || typeof value.source !== 'string' ||
        typeof value.timestamp !== 'string' || !object(value.payload)) return null;
    return value as unknown as MonitorEvent;
  } catch { return null; }
}
export function agentStatus(agent: Agent): string {
  if (agent.status !== 'running') return agent.status;
  if (!agent.activity) return 'activity unavailable';
  return agent.activity.state === 'working' ? 'running' : agent.activity.state;
}
export function connectionState(reachable: boolean | null, streamOpen: boolean): Connection {
  if (reachable === null) return 'connecting';
  if (!reachable) return 'disconnected';
  return streamOpen ? 'connected' : 'reconnecting';
}
export async function snapshot(signal?: AbortSignal): Promise<Snapshot> {
  const [agents, tasks, runs, workspaces] = await Promise.all([
    get<{ agents: Agent[] }>('/api/agents', signal), get<{ items: Task[] }>('/api/work', signal),
    get<{ runs: WorkflowRun[] }>('/api/workflows/runs', signal),
    get<{ worktrees: Workspace[] }>('/api/workspaces', signal),
  ]);
  const enriched = await Promise.all(tasks.items.map(async task => {
    if (task.assigned_agent || !['in_progress', 'running'].includes(task.status)) return task;
    try { return (await taskDetail(task.item_id, signal)).item; }
    catch (error) { if (signal?.aborted) throw error; return task; }
  }));
  return { agents: agents.agents, tasks: enriched, runs: runs.runs, workspaces: workspaces.worktrees };
}
export const taskDetail = (id: string, signal?: AbortSignal) => get<{ item: TaskDetail }>(`/api/work/${encodeURIComponent(id)}`, signal);
export const branchDiff = (id: string, signal?: AbortSignal) => get<{ diff: string }>(`/api/workspaces/${encodeURIComponent(id)}/diff?full=true`, signal);
export function taskRelationships(task: Task, workspaces: Workspace[]) {
  const assignment = [...(task.history || [])].reverse().find(entry => entry.action === 'assigned');
  const agentId = task.assigned_agent || (typeof assignment?.agent === 'string' ? assignment.agent : null);
  const workspaceIds = workspaces.filter(w => w.work_item_id === task.item_id).map(w => w.worktree_id);
  if (typeof assignment?.worktree_id === 'string' && !workspaceIds.includes(assignment.worktree_id)) {
    workspaceIds.push(assignment.worktree_id);
  }
  return { agentId, workspaceIds };
}
export const appendEvent = (events: MonitorEvent[], event: MonitorEvent) => [...events.slice(-999), event];
