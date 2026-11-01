import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { MergeEligibility, MergeOperation, MergeRequest } from './types';

// Exercise hook transitions without adding a DOM dependency to the test suite.
const harness = vi.hoisted(() => ({
  slots: [] as any[], cursor: 0, effects: [] as (() => void)[], dirty: false,
  resources: new Map<string, unknown>(), get: vi.fn(), post: vi.fn(),
}));
vi.mock('react', () => ({
  useRef: (value: unknown) => {
    const index = harness.cursor++;
    return harness.slots[index] ??= { current: value };
  },
  useState: (initial: unknown) => {
    const index = harness.cursor++;
    if (!(index in harness.slots)) harness.slots[index] = typeof initial === 'function' ? initial() : initial;
    return [harness.slots[index], (next: any) => {
      const value = typeof next === 'function' ? next(harness.slots[index]) : next;
      if (!Object.is(value, harness.slots[index])) harness.dirty = true;
      harness.slots[index] = value;
    }];
  },
  useEffect: (effect: () => void, dependencies: unknown[]) => {
    const index = harness.cursor++;
    const previous = harness.slots[index] as unknown[] | undefined;
    if (!previous || dependencies.some((value, i) => !Object.is(value, previous[i]))) harness.effects.push(effect);
    harness.slots[index] = dependencies;
  },
}));
vi.mock('./api', async importOriginal => ({
  ...await importOriginal<typeof import('./api')>(), get: harness.get, post: harness.post,
}));
vi.mock('./useResource', () => ({
  useResource: (path: string | null) => path && harness.resources.get(path) || { error: '' },
}));
import { useWorkspaceMerge } from './useWorkspaceMerge';

const endpoint = '/api/workspaces/source';
const storageKey = 'workspace-merge:source';
const operationId = '00000000-0000-4000-8000-000000000000';
const request: MergeRequest = {
  operation_id: operationId, source_branch: 'feature', source_sha: 'old-source',
  destination_workspace_id: 'main', destination_path: '/repo', destination_sha: 'old-main',
};
const accepted: MergeOperation = {
  ...request, source_workspace_id: 'source', created_at: '2026-11-01T12:00:00', state: 'accepted',
  resulting_sha: null, error_code: null, error_message: null, diagnostics: null, conflict_paths: [],
};
const completed: MergeOperation = { ...accepted, state: 'merged', resulting_sha: 'merged-sha' };
const eligibility: MergeEligibility = {
  ...request, source_workspace_id: 'source', eligible: true, blockers: [], latest_operation: null,
};

function render() {
  for (let count = 0; count < 10; count++) {
    harness.cursor = 0;
    harness.dirty = false;
    const result = useWorkspaceMerge('source');
    harness.effects.splice(0).forEach(effect => effect());
    if (!harness.dirty) return result;
  }
  throw new Error('Hook did not settle');
}

beforeEach(() => {
  harness.slots = [];
  harness.effects = [];
  harness.resources.clear();
  harness.get.mockReset();
  harness.post.mockReset();
  const storage = new Map<string, string>();
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => storage.get(key) ?? null,
    setItem: (key: string, value: string) => storage.set(key, value),
    removeItem: (key: string) => storage.delete(key),
  });
  vi.stubGlobal('window', new EventTarget());
  harness.resources.set(`${endpoint}/merge`, { data: eligibility, error: '' });
});
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('saved merge reconciliation', () => {
  it.each((['merged', 'already_integrated', 'failed'] as const).flatMap(state =>
    (['before', 'after'] as const).map(timing => ({ state, timing }))))(
    'removes obsolete timeout guidance when POST fails $timing polling confirms $state', async ({ state, timing }) => {
      let reject!: (reason: Error) => void;
      harness.get.mockResolvedValue(eligibility);
      harness.post.mockReturnValue(new Promise((_resolve, fail) => { reject = fail; }));
      vi.spyOn(crypto, 'randomUUID').mockReturnValue(operationId);
      const submission = render().merge();
      await Promise.resolve();
      expect(harness.post).toHaveBeenCalledOnce();
      if (timing === 'before') {
        reject(new Error('POST timed out'));
        await submission;
        expect(render().error).toContain('Check the saved attempt before retrying.');
      }
      const terminalResult = { ...completed, state,
        error_message: state === 'failed' ? 'Merge conflict. No changes were merged.' : null };
      harness.resources.set(`${endpoint}/merges/${operationId}`, { data: { operation: terminalResult }, error: '' });
      expect(render().operation).toEqual(terminalResult);
      expect(localStorage.getItem(storageKey)).toBeNull();
      harness.resources.delete(`${endpoint}/merges/${operationId}`);
      if (timing === 'after') {
        reject(new Error('POST timed out'));
        await submission;
      }
      const result = render();
      expect(result.operation).toEqual(terminalResult);
      expect(result.error).toBe('');
      expect(result.busy).toBe(false);
      expect(result.uncertain).toBe(false);
    });

  it.each(['merged', 'already_integrated', 'failed'] as const)(
    'retains %s when POST acceptance arrives after polling completion', async state => {
      let respond!: (value: { operation: MergeOperation }) => void;
      harness.get.mockResolvedValue(eligibility);
      harness.post.mockReturnValue(new Promise(resolve => { respond = resolve; }));
      vi.spyOn(crypto, 'randomUUID').mockReturnValue(operationId);
      const submission = render().merge();
      await Promise.resolve();
      expect(harness.post).toHaveBeenCalledOnce();
      const terminalResult = { ...completed, state };
      harness.resources.set(`${endpoint}/merges/${operationId}`, { data: { operation: terminalResult }, error: '' });
      expect(render().operation).toEqual(terminalResult);
      expect(localStorage.getItem(storageKey)).toBeNull();
      // Pending polling is now cleared; eligibility has never seen completion.
      harness.resources.delete(`${endpoint}/merges/${operationId}`);
      respond({ operation: accepted });
      await submission;
      const result = render();
      expect(result.operation).toEqual(terminalResult);
      expect(result.busy).toBe(false);
      expect(result.submitting).toBe(false);
      expect(result.uncertain).toBe(false);
    });

  it('retains completion when a saved-status response arrives after polling completion', async () => {
    let respond!: (value: { operation: MergeOperation }) => void;
    localStorage.setItem(storageKey, JSON.stringify(request));
    harness.get.mockReturnValue(new Promise(resolve => { respond = resolve; }));
    const checking = render().merge();
    harness.resources.set(`${endpoint}/merges/${operationId}`, { data: { operation: completed }, error: '' });
    expect(render().operation).toEqual(completed);
    harness.resources.delete(`${endpoint}/merges/${operationId}`);
    respond({ operation: accepted });
    await checking;
    expect(render().operation).toEqual(completed);
    expect(render().busy).toBe(false);
    expect(harness.post).not.toHaveBeenCalled();
  });

  it('checks a completed saved attempt without submitting displayed newer commits', async () => {
    localStorage.setItem(storageKey, JSON.stringify(request));
    harness.resources.set(`${endpoint}/merge`, { data: { ...eligibility, source_sha: 'new-source' }, error: '' });
    harness.get.mockResolvedValue({ operation: completed });
    const initial = render();
    expect(initial.uncertain).toBe(true);
    await initial.merge();
    const result = render();
    expect(harness.get).toHaveBeenCalledExactlyOnceWith(`${endpoint}/merges/${operationId}`);
    expect(harness.post).not.toHaveBeenCalled();
    expect(result.operation).toEqual(completed);
    expect(result.busy).toBe(false);
    expect(localStorage.getItem(storageKey)).toBeNull();
  });

  it('shows terminal eligibility over cached acceptance and retains it after storage removal', async () => {
    harness.get.mockResolvedValue(eligibility);
    harness.post.mockResolvedValue({ operation: accepted });
    vi.spyOn(crypto, 'randomUUID').mockReturnValue(operationId);
    await render().merge();
    expect(render().busy).toBe(true);
    // Another tab clears storage before this tab has polled the operation.
    localStorage.removeItem(storageKey);
    window.dispatchEvent(Object.assign(new Event('storage'), { key: storageKey }));
    expect(render().busy).toBe(true);
    harness.resources.set(`${endpoint}/merge`, { data: { ...eligibility, latest_operation: completed }, error: '' });
    const result = render();
    expect(result.operation).toEqual(completed);
    expect(result.busy).toBe(false);
    expect(result.uncertain).toBe(false);
    // A delayed accepted response must not reopen the completed attempt.
    harness.resources.set(`${endpoint}/merge`, { data: { ...eligibility, latest_operation: accepted }, error: '' });
    expect(render().operation).toEqual(completed);
    expect(render().busy).toBe(false);
  });

  it('continues polling when another tab clears storage before a terminal result arrives', () => {
    localStorage.setItem(storageKey, JSON.stringify(request));
    render();
    localStorage.removeItem(storageKey);
    window.dispatchEvent(Object.assign(new Event('storage'), { key: storageKey }));
    expect(render().uncertain).toBe(true);
    harness.resources.set(`${endpoint}/merges/${operationId}`, { data: { operation: completed }, error: '' });
    expect(render().operation).toEqual(completed);
    harness.resources.delete(`${endpoint}/merges/${operationId}`);
    expect(render().operation).toEqual(completed);
    expect(render().busy).toBe(false);
  });
});
