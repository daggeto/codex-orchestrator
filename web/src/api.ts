import type { Agent, Connection, JsonObject, MonitorEvent, Snapshot, Task, TaskDetail, WorkflowRun, Workspace } from './types';

export class ApiError extends Error {
  constructor(message: string, public status: number, public details?: unknown) { super(message); }
}
export async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  const timeout = AbortSignal.timeout(5000);
  const response = await fetch(path, { signal: signal ? AbortSignal.any([signal, timeout]) : timeout });
  if (!response.ok) {
    const result = await response.json().catch(() => null);
    const endpoint = path.split('?')[0];
    const missingRepositoryApi = response.status === 404 &&
      ['/api/navigation', '/api/repositories', '/api/filesystem/directories'].includes(endpoint);
    const message = typeof result?.error === 'string' ? result.error : missingRepositoryApi
      ? 'The service is missing the repository API. After active tasks finish, restart the service from the current checkout with make start, then reload this page.'
      : `Request failed (${response.status})`;
    throw new ApiError(message, response.status, result);
  }
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
    if (task.history !== undefined) return task;
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

export async function post<T>(path: string, body: unknown, timeout = 15000): Promise<T> {
  const response = await fetch(path, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body), signal: AbortSignal.timeout(timeout),
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(typeof result.error === 'string' ? result.error : `Request failed (${response.status})`, response.status, result);
  return result as T;
}

// File bodies bypass the JSON helper and remain streaming request bodies.
export async function uploadAttachment(draftId: string, uploadId: string, file: File, signal?: AbortSignal) {
  const response = await fetch(`/api/attachment-drafts/${encodeURIComponent(draftId)}/files/${encodeURIComponent(uploadId)}`, {
    method: 'PUT', headers: { 'Content-Type': 'application/octet-stream', 'X-Filename': encodeURIComponent(file.name) },
    body: file, signal,
  });
  const result = await response.json().catch(() => ({}));
  if (!response.ok) throw new ApiError(result.error || `Upload failed (${response.status})`, response.status, result);
  return result as import('./types').Attachment;
}
const submissionKeys = new Map<string, { fingerprint: string; key: string }>();
export function clearSubmissionIdentity(storageKey: string) {
  submissionKeys.delete(storageKey);
  try { sessionStorage.removeItem(storageKey); } catch { /* Optional storage. */ }
}
export function submissionIdentity(storageKey: string, payload: unknown): string {
  const fingerprint = JSON.stringify(payload);
  try {
    const previous = JSON.parse(sessionStorage.getItem(storageKey) || 'null');
    if (previous?.fingerprint === fingerprint && typeof previous.key === 'string') return previous.key;
  } catch { /* Allocate a fresh identity when storage is unavailable. */ }
  const remembered = submissionKeys.get(storageKey);
  if (remembered?.fingerprint === fingerprint) return remembered.key;
  const key = crypto.randomUUID();
  submissionKeys.set(storageKey, { key, fingerprint });
  try { sessionStorage.setItem(storageKey, JSON.stringify({ key, fingerprint })); } catch { /* In-memory callers retain the key. */ }
  return key;
}
