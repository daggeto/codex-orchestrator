import { describe, expect, it } from 'vitest';
import type { DurableEvent } from './types';
import { reduceConversation } from './conversation';
const event = (id: number, kind: string, item_id: string | null, payload: Record<string, unknown> = {}, thread_id = 'thread'): DurableEvent => ({ id, kind, item_id, payload, turn_id: 'turn', thread_id, agent_id: 'agent', created_at: '' });
describe('conversation reconciliation', () => {
  it('replaces deltas by item and preserves multiple replies across tools and replay', () => {
    const records = [event(1, 'item/agentMessage/delta', 'a', { delta: 'Hel' }), event(2, 'item/started', 'tool', { item: { type: 'commandExecution' } }), event(3, 'item/completed', 'a', { item: { type: 'agentMessage', text: 'Hello' } }), event(4, 'item/completed', 'b', { item: { type: 'agentMessage', text: 'Second reply' } })];
    expect(reduceConversation([...records, ...records]).filter(e => e.kind === 'assistant').map(e => e.text)).toEqual(['Hello', 'Second reply']);
    expect(reduceConversation(records.slice(0, 2)).length).toBe(2);
  });
  it('merges only contiguous anonymous deltas and retains uncertain final messages', () => {
    const rows = [event(1, 'item/agentMessage/delta', null, { delta: 'A' }), event(2, 'item/agentMessage/delta', null, { delta: 'B' }), event(3, 'tool', null), event(4, 'item/agentMessage/delta', null, { delta: 'C' }), event(5, 'item/completed', null, { item: { type: 'agentMessage', text: 'ABC' } })];
    expect(reduceConversation(rows).filter(e => e.kind === 'assistant').map(e => e.text)).toEqual(['AB', 'C', 'ABC']);
  });
  it('keeps failures visible and thread identity separate', () => {
    const entries = reduceConversation([event(1, 'item/completed', 'tool', { item: { type: 'commandExecution', exitCode: 1 } }), event(2, 'item/agentMessage/delta', 'a', { delta: 'one' }), event(3, 'item/agentMessage/delta', 'a', { delta: 'two' }, 'other')]);
    expect(entries.map(e => e.kind)).toEqual(['error', 'assistant', 'assistant']);
  });
  it('does not append late deltas after final content', () => {
    expect(reduceConversation([event(1, 'item/completed', 'a', { item: { type: 'agentMessage', text: 'Done' } }), event(2, 'item/agentMessage/delta', 'a', { delta: 'ne' })])[0].text).toBe('Done');
  });
});
it('retains anonymous content next to an identified stream', () => {
  const rows = [event(1, 'item/agentMessage/delta', 'a', { delta: 'A' }), event(2, 'item/agentMessage/delta', null, { delta: 'B' }), event(3, 'item/completed', 'a', { item: { type: 'agentMessage', text: 'A' } })];
  expect(reduceConversation(rows).map(e => e.text)).toEqual(['A', 'B']);
});
it('promotes Claude and nested Codex failures', () => {
  const rows = [event(1, 'item/completed', 'tool', { item: { type: 'tool_result', is_error: true, content: 'Permission denied' } }), event(2, 'turn/completed', null, { is_error: true, result: 'Failed turn' }), event(3, 'turn/completed', null, { turn: { status: 'failed', error: 'Backend failed' } })];
  expect(reduceConversation(rows).map(e => e.kind)).toEqual(['error', 'error', 'error']);
  expect(reduceConversation(rows)[0].text).toBe('Permission denied');
});

it('retains ordered attachment references on attachment-only messages and replay', () => {
  const record = event(1, 'user', null, { text: '', attachment_ids: ['image', 'file'] });
  expect(reduceConversation([record, record])).toEqual([expect.objectContaining({ kind: 'user', text: '', attachmentIds: ['image', 'file'] })]);
});
