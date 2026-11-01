import { isValidElement, type ReactElement, type ReactNode } from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { Chat } from './Chat';
import { TaskComposer } from './TaskComposer';

// Drive the composer's real handlers without mounting unrelated chat rendering.
const harness = vi.hoisted(() => ({ slots: [] as any[], cursor: 0, post: vi.fn(), navigate: vi.fn() }));
vi.mock('react', async importOriginal => ({
  ...await importOriginal<typeof import('react')>(),
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
vi.mock('../api', () => ({ post: harness.post }));
vi.mock('./Chat', () => ({ Chat: () => null }));
vi.mock('../useResource', () => ({
  useResource: (path: string) => ({ data: path === '/api/workflows' ? { workflows: [
    { name: 'small-change', publication_policy: 'none' },
    { name: 'feature', publication_policy: 'optional' },
    { name: 'required', publication_policy: 'required' },
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
  const select = toolbar().find(element => element.type === 'select' &&
    ['small-change', 'feature', 'required'].includes(element.props.value))!;
  select.props.onChange({ target: { value: name } });
}
function checkbox() { return toolbar().find(element => element.type === 'input' && element.props.type === 'checkbox'); }

beforeEach(() => {
  harness.slots = [];
  harness.post.mockReset().mockResolvedValue({ task_id: 'task' });
  harness.navigate.mockReset();
});

describe('workflow publication settings', () => {
  it('ignores checked feature publication after switching to small-change', async () => {
    selectWorkflow('feature');
    checkbox()!.props.onChange({ target: { checked: true } });
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
    checkbox()!.props.onChange({ target: { checked: publish } });
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
