import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Chat } from './Chat';
import { TaskComposer } from './TaskComposer';
import { NativeSelect } from './ui/primitives/native-select';
import { Checkbox } from './ui/primitives/checkbox';

// Drive the composer's real handlers without mounting unrelated chat rendering.
const harness = vi.hoisted(() => ({ slots: [] as any[], cursor: 0, post: vi.fn(), navigate: vi.fn() }));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
  useEffect: () => {},
  useState: (initial: unknown) => {
    const index = harness.cursor++;
    if (!(index in harness.slots)) harness.slots[index] = initial;
    return [harness.slots[index], (value: unknown) => { harness.slots[index] = value; }];
  },
  useRef: (initial: unknown) => {
    const index = harness.cursor++;
    return harness.slots[index] ??= { current: initial };
  },
}));
vi.mock('react-router', () => ({ useNavigate: () => harness.navigate }));
vi.mock('../api', () => ({ post: harness.post, submissionIdentity: () => 'stable-key', clearSubmissionIdentity: () => {} }));
vi.mock('./Chat', () => ({ Chat: () => null }));
// Operation ordering is covered separately; these tests assert composer payloads.
vi.mock('../useOperation', () => ({
  useOperation: () => ({
    current: undefined, unresolved: false, durable: true,
    prepare: (request: unknown, draftVersion: number, id: string) => ({ request, draftVersion, id }),
    settle: (_attempt: unknown, status: string) => ({ status }),
    isCurrent: () => true,
  }),
}));
vi.mock('../useResource', () => ({
  useResource: (path: string) => ({ data: path === '/api/workflows' ? { workflows: [
    { name: 'small-change', publication_policy: 'none', required_roles: [] },
    { name: 'feature', publication_policy: 'optional', required_roles: [] },
    { name: 'required', publication_policy: 'required', required_roles: [] },
  ] } : {}, error: '' }),
}));

type Element = ReactElement<Record<string, any>>;
function elements(node: ReactNode): Element[] {
  if (Array.isArray(node)) return node.flatMap(elements);
  if (!isValidElement<Record<string, any>>(node)) return [];
  return [node, ...elements(node.props.children)];
}
function render() {
  harness.cursor = 0;
  return TaskComposer({ workspace: { workspace_id: 'workspace', repository_id: 'repo',
    kind: 'worktree', path: '/repo', branch: 'feature', status: 'active' } });
}
function chat() { return elements(render()).find(element => element.type === Chat)!; }
function toolbar() { return elements(chat().props.toolbar); }
function selectWorkflow(name: string) {
  const select = toolbar().find(element => element.type === NativeSelect &&
    ['small-change', 'feature', 'required'].includes(element.props.value))!;
  select.props.onChange({ target: { value: name } });
}
function checkbox() { return toolbar().find(element => element.type === Checkbox); }

beforeEach(() => {
  harness.slots = [];
  harness.post.mockReset().mockResolvedValue({ task_id: 'task' });
  harness.navigate.mockReset();
});

describe('workflow publication settings', () => {
  it('ignores checked feature publication after switching to small-change', async () => {
    selectWorkflow('feature');
    checkbox()!.props.onCheckedChange(true);
    expect(checkbox()!.props.checked).toBe(true);
    selectWorkflow('small-change');
    expect(checkbox()).toBeUndefined();
    await chat().props.onSubmit('Make a small change');
    expect(harness.post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
      settings: { workflow: 'small-change', variables: { create_mr: false } },
    }));
  });

  it.each([false, true])('respects optional publication selection %s', async publish => {
    selectWorkflow('feature');
    checkbox()!.props.onCheckedChange(publish);
    await chat().props.onSubmit('Implement feature');
    expect(harness.post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
      settings: { workflow: 'feature', variables: { create_mr: publish } },
    }));
  });

  it('enables required publication independently of the unchecked optional setting', async () => {
    selectWorkflow('feature');
    expect(checkbox()!.props.checked).toBe(false);
    selectWorkflow('required');
    expect(checkbox()).toBeUndefined();
    await chat().props.onSubmit('Publish approved changes');
    expect(harness.post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
      settings: { workflow: 'required', variables: { create_mr: true } },
    }));
  });
});

it('submits attachment-only requests without dropping upload identity', async () => {
  await chat().props.onSubmit('', { draft_id: 'draft', attachment_ids: ['image'] });
  expect(harness.post).toHaveBeenCalledWith('/api/tasks', expect.objectContaining({
    prompt: '', draft_id: 'draft', attachment_ids: ['image'], idempotency_key: 'stable-key',
  }));
});
