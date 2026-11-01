import { describe, expect, it, vi } from 'vitest';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import type { TaskRecord } from './types';
import { TaskRow, taskStatus } from './components/TaskRow';
import { TaskContext } from './components/TaskContext';
import { TaskList } from './components/TaskList';

vi.mock('./navigation', () => ({
  useNavigation: () => ({ data: {
    repositories: [{ repository_id: 'a', name: 'Alpha' }, { repository_id: 'b', name: 'Beta' }],
    workspaces: [
      { workspace_id: 'a-main', repository_id: 'a', kind: 'main' },
      { workspace_id: 'b-main', repository_id: 'b', kind: 'main' },
    ],
  } }),
}));

const task: TaskRecord = {
  task_id: 'task', workspace_id: 'a-main', title: 'Same title', prompt: 'Prompt',
  kind: 'agent', archived_at: null, status: 'active', phase: 'Turn completed with error',
  last_turn: { ok: false }, settings: {}, created_at: 'now',
};

describe('task recognition', () => {
  it.each(['Turn completed with error', 'Disconnected', 'Ready for your next message'])('uses the failed outcome regardless of phase: %s', phase => {
    const failed = { ...task, phase };
    expect(taskStatus(failed)).toBe('Needs attention');
    const row = renderToStaticMarkup(<MemoryRouter><TaskRow task={failed} /></MemoryRouter>);
    const header = renderToStaticMarkup(<MemoryRouter><TaskContext task={failed} /></MemoryRouter>);
    expect(row).toContain('Needs attention');
    expect(row).toContain('status-symbol failed');
    expect(header).toContain('Needs attention');
    expect(row + header).not.toContain('Working');
  });

  it('distinguishes running, ready, waiting, and terminal states', () => {
    expect(taskStatus({ ...task, phase: 'Agent running', last_turn: null })).toBe('Working');
    expect(taskStatus({ ...task, phase: 'retry in progress', last_turn: null })).toBe('Working');
    expect(taskStatus({ ...task, status: 'queued' })).toBe('queued');
    expect(taskStatus({ ...task, phase: 'Ready for your next message', last_turn: { ok: true } })).toBe('Ready');
    expect(taskStatus({ ...task, status: 'waiting_input' })).toBe('Waiting for me');
    expect(taskStatus({ ...task, status: 'completed' })).toBe('Finished');
    expect(taskStatus({ ...task, status: 'failed' })).toBe('Needs attention');
  });

  it.each([null, 'now'])('identifies repositories beside identical Main tasks, archived_at=%s', archived_at => {
    const tasks = [task, { ...task, task_id: 'other', workspace_id: 'b-main' }].map(t => ({ ...t, archived_at }));
    const markup = renderToStaticMarkup(<MemoryRouter><TaskList tasks={tasks} actions={() => <button>Restore</button>} /></MemoryRouter>);
    expect(markup).toContain('<small>Alpha · Main</small>');
    expect(markup).toContain('<small>Beta · Main</small>');
    expect(markup.match(/Restore/g)).toHaveLength(2);
  });
});
