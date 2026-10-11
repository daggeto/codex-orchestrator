import { afterEach, describe, expect, it, vi } from 'vitest';
import { agentStatus, appendEvent, ApiError, branchDiff, connectionState, get, parseEvent, snapshot, taskRelationships } from './api';
import type { Agent, MonitorEvent, Task, TaskDetail, Workspace } from './types';

const event: MonitorEvent = { topic: 'new.unknown.topic', source: 'registry', payload: { text: '<script>hello</script>' }, timestamp: '2026-10-11T12:00:00Z' };
const task: TaskDetail = {
  item_id: 'task', title: 'Example', status: 'in_progress', priority: 1, assigned_agent: null,
  tags: [], created_at: '', updated_at: '', completed_at: null, description: '', assigned_thread: null,
  workflow_id: null, parent_item: null, input_data: {}, output_data: {},
  history: [{ action: 'assigned', agent: 'old', worktree_id: 'old-wt' },
    { action: 'assigned', agent: 'current', worktree_id: 'historical' }],
};
const workspace = { worktree_id: 'registered', work_item_id: 'task', agent_id: 'current' } as Workspace;
afterEach(() => vi.unstubAllGlobals());

describe('monitor data and events', () => {
  it('keeps unknown topics and text payloads intact', () => {
    expect(parseEvent(JSON.stringify(event))).toEqual(event);
  });
  it.each(['broken', 'null', '[]', '{}', '{"topic":1}', JSON.stringify({ ...event, payload: [] })])('ignores malformed SSE data: %s', value => {
    expect(parseEvent(value)).toBeNull();
  });
  it('bounds the event buffer to the newest 1000', () => {
    const first = { ...event, topic: 'first' };
    const events = [first, ...Array.from({ length: 999 }, () => event)];
    const next = appendEvent(events, { ...event, topic: 'last' });
    expect(next).toHaveLength(1000);
    expect(next[0].topic).not.toBe('first');
    expect(next.at(-1)?.topic).toBe('last');
    expect(events[0]).toBe(first);
  });
  it.each(['stopped', 'starting', 'degraded', 'stopping', 'error'])('preserves process status %s', status => {
    expect(agentStatus({ status, activity: { state: 'idle', threads: [], error: null } } as unknown as Agent)).toBe(status);
  });
  it.each([['idle', 'idle'], ['working', 'running'], ['error', 'error']])('maps activity %s', (state, expected) => {
    expect(agentStatus({ status: 'running', activity: { state, threads: [], error: null } } as unknown as Agent)).toBe(expected);
  });
  it('does not label missing activity as idle', () => {
    expect(agentStatus({ status: 'running' } as Agent)).toBe('activity unavailable');
  });
  it('distinguishes initial, online, stream interruption, offline, and recovery', () => {
    expect([connectionState(null, false), connectionState(true, true), connectionState(true, false),
      connectionState(false, false), connectionState(false, true), connectionState(true, true)]).toEqual([
      'connecting', 'connected', 'reconnecting', 'disconnected', 'disconnected', 'connected',
    ]);
  });
  it('uses explicit task IDs and the latest assignment history', () => {
    expect(taskRelationships(task, [workspace])).toEqual({ agentId: 'current', workspaceIds: ['registered', 'historical'] });
    expect(taskRelationships({ ...task, assigned_agent: 'explicit' }, [])).toEqual({ agentId: 'explicit', workspaceIds: ['historical'] });
    expect(taskRelationships({ ...task, history: [] }, [{ ...workspace, work_item_id: 'different' }])).toEqual({ agentId: null, workspaceIds: [] });
  });
  it('normalizes real snapshot envelopes and resolves active assignment history', async () => {
    vi.stubGlobal('fetch', vi.fn(async (path: string) => {
      const bodies: Record<string, unknown> = {
        '/api/agents': { agents: [] }, '/api/work': { items: [task as Task] },
        '/api/workflows/runs': { runs: [] }, '/api/workspaces': { worktrees: [workspace] },
        '/api/work/task': { item: task },
      };
      return new Response(JSON.stringify(bodies[path]));
    }));
    const data = await snapshot();
    expect(data.tasks[0].history).toEqual(task.history);
    expect(data.workspaces).toEqual([workspace]);
    expect(data.agents).toEqual([]);
  });
  it('encodes selected IDs and distinguishes missing diffs', async () => {
    const fetch = vi.fn(async (_path: string, _options?: RequestInit) => new Response('{"error":"missing"}', { status: 404 }));
    vi.stubGlobal('fetch', fetch);
    await expect(branchDiff('a/b')).rejects.toMatchObject({ status: 404 });
    expect(fetch.mock.calls[0][0]).toBe('/api/workspaces/a%2Fb/diff?full=true');
  });
  it('propagates request failures and cancellation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('', { status: 500 })));
    await expect(get('/health')).rejects.toBeInstanceOf(ApiError);
    const controller = new AbortController();
    controller.abort();
    vi.stubGlobal('fetch', vi.fn(async (_path, options) => { options.signal.throwIfAborted(); }));
    await expect(get('/health', controller.signal)).rejects.toMatchObject({ name: 'AbortError' });
  });
});
